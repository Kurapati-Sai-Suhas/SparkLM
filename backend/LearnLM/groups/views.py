import os
import json
import io
import logging
import zipfile
import fitz  # PyMuPDF
import PyPDF2

logger = logging.getLogger(__name__)

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Avg, Sum, Q
from django.core.files.base import ContentFile  # 👈 NEW: Required to save extracted bytes as real files

from rest_framework import viewsets, generics, filters, permissions, parsers, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied, ValidationError

from django_filters.rest_framework import DjangoFilterBackend
from langchain_text_splitters import RecursiveCharacterTextSplitter

from common.throttling import ClientIPScopedRateThrottle
from common.storage import signed_url
from common.authorization import (
    accessible_documents,
    accessible_groups as _accessible_groups,
    accessible_materials as _accessible_materials,
    resolve_group,
)

# ── Single clean import block — no duplicates ────────────────
from .ai_services import AIService, VectorSearchService, RAGService
from .hybrid_router import route_recommendation, HierarchicalEngine, get_mastered_topic_names
from .utils import extract_text_from_file, load_image_for_ai
from .models import (
    StudyGroup, StudyMaterial, QuizResult, UserActivity,
    AssignedQuiz, Connection, DirectMessage, Document, GroupMessage
)
from .serializers import (
    ConnectionSerializer, QuizResultSerializer, StudyGroupSerializer,
    UserBasicSerializer, UserDisplaySerializer, UserSerializer,
    StudyMaterialSerializer, AssignedQuizSerializer,
    HybridRouterSerializer
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────

class LargePagination(PageNumberPagination):
    # 8 was still below the size of a real group (StudyGroup.capacity defaults
    # to 50), so the roster and the file library both truncated. 50 covers a
    # full-capacity group in one page; `page_size` lets a caller ask for less,
    # and clients follow `next` for the rest (M2 P2.1).
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000


# ─────────────────────────────────────────────────────────────
# Auth & User
# ─────────────────────────────────────────────────────────────

class CreateUserView(generics.CreateAPIView):
    serializer_class = UserSerializer
    # Explicit public opt-in (M4 WP0). Registration is one of six endpoints
    # allowed to be reached without a token; see the authorization matrix in
    # common/test_authorization_matrix.py, which fails if this set changes.
    permission_classes = [AllowAny]


class UserDashboardStats(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        active_groups_count = StudyGroup.objects.filter(
            Q(members=user) | Q(creator=user)
        ).distinct().count()
        created_count = StudyGroup.objects.filter(creator=user).count()
        joined_count  = StudyGroup.objects.filter(members=user).count()
        return Response({
            "username":           user.username,
            "active_groups":      active_groups_count,
            "created_groups":     created_count,
            "joined_groups":      joined_count,
            "study_hours":        0,
            "quizzes_taken":      0,
            "achievement_points": 100,
            # Lets the frontend skip the staff-only MLOps telemetry fetch
            # entirely for the ~all-users case it always 403s for, instead
            # of paying a full request's latency for a call that's known
            # to fail. Deliberately not added to UserSerializer, which
            # CreateUserView also uses for registration writes.
            "is_staff":           user.is_staff,
        })


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────────
# Study Groups
# ─────────────────────────────────────────────────────────────

class StudyGroupViewSet(viewsets.ModelViewSet):
    serializer_class   = StudyGroupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields   = ['id', 'join_code', 'capacity']
    search_fields      = ['name', 'description', 'join_code']
    pagination_class   = LargePagination

    def get_queryset(self):
        user = self.request.user
        # select_related/prefetch_related are load-bearing now that a page
        # holds 50 rows instead of 8: StudyGroupSerializer nests `creator`,
        # `members` and `active_portals`, so without them each group costs
        # three extra queries and a full page costs 150 (P2.1).
        #
        # '-id' breaks ties: created_at is auto_now_add, so groups made in the
        # same instant sort arbitrarily and could shuffle between pages (P2.1).
        return StudyGroup.objects.filter(
            Q(members=user) | Q(creator=user)
        ).distinct().select_related('creator').prefetch_related(
            'members', 'active_portals'
        ).order_by('-created_at', '-id')

    def perform_create(self, serializer):
        group = serializer.save(creator=self.request.user)
        group.members.add(self.request.user)

    def get_throttles(self):
        """
        Dedicated bucket for `join` (M4 WP5) — see 'group-join' in settings.

        Set here rather than via @action(throttle_classes=...) because
        ClientIPScopedRateThrottle reads `self.throttle_scope` off the view,
        and DRF resolves throttles in initial() — before the handler body
        runs, so assigning the scope inside join() would be too late.
        `self.action` is already populated by initialize_request().
        """
        if getattr(self, 'action', None) == 'join':
            self.throttle_scope = 'group-join'
            return [ClientIPScopedRateThrottle()]
        return super().get_throttles()

    @action(detail=False, methods=['post'])
    def join(self, request):
        code = request.data.get('code')
        if not code:
            return Response({'error': 'Code is required'}, status=400)
        try:
            group = StudyGroup.objects.get(join_code=code)
            if group.members.count() >= group.capacity:
                return Response({'error': 'Group is full!'}, status=400)
            if request.user in group.members.all():
                return Response({'message': 'Already a member', 'id': group.id}, status=200)
            group.members.add(request.user)
            return Response({'message': 'Joined successfully!', 'id': group.id})
        except StudyGroup.DoesNotExist:
            return Response({'error': 'Invalid Group Code'}, status=404)

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        group = self.get_object()
        if request.user not in group.members.all():
            return Response({"Message": "You are not a member of this group."}, status=400)
        group.members.remove(request.user)
        return Response({"Message": "You have left the group."}, status=200)


# ─────────────────────────────────────────────────────────────
# Study Materials & Document Extraction Pipeline
# ─────────────────────────────────────────────────────────────

def extract_images_from_document(file_obj, filename):
    """
    Cracks open PDFs and DOCX files to extract raw images directly from memory.
    Returns a list of io.BytesIO image objects ready for MobileNetV2.

    Best-effort: always returns a list, never raises. The caller runs inside
    an upload, and losing a user's file because one diagram could not be
    read is never the right trade.
    """
    images = []
    lower_name = filename.lower()

    try:
        # Inside the try. This read was previously above it, so an I/O error
        # here escaped the function and 500'd the upload — reachable on
        # Render's ephemeral filesystem, where a file can vanish between
        # request and read. Found by a regression test that stubbed this
        # function to raise and got a 500 instead of a logged warning.
        file_bytes = file_obj.read()
        file_obj.seek(0)  # reset so Django can still save the original file

        if lower_name.endswith('.pdf'):
            logger.info("Scanning PDF %s for diagrams", filename)
            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(pdf_doc)):
                page = pdf_doc.load_page(page_num)
                for img in page.get_images(full=True):
                    xref = img[0]
                    base_image = pdf_doc.extract_image(xref)
                    images.append(io.BytesIO(base_image["image"]))
                    
        elif lower_name.endswith('.docx'):
            logger.info("Unzipping DOCX %s for diagrams", filename)
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx_zip:
                for item in docx_zip.namelist():
                    # Microsoft Word secretly stores all images in this internal folder
                    if item.startswith('word/media/') and item.lower().endswith(('.png', '.jpeg', '.jpg')):
                        images.append(io.BytesIO(docx_zip.read(item)))
    except Exception:
        # Best-effort by design: a document whose images cannot be read must
        # still upload. Logged rather than printed so a systematic failure
        # is visible in Sentry instead of lost on stdout.
        logger.exception("Image extraction failed for %s", filename)

    logger.info("Found %d embedded images in %s", len(images), filename)
    return images


# Authorization predicates now live in common/authorization.py — one
# definition per resource for the whole repository (M4 security sprint, WP1).
# Re-exported here because existing call sites and tests import them from
# this module; the definitions themselves are unchanged.
accessible_materials = _accessible_materials
accessible_groups = _accessible_groups


def cache_extracted_text(material):
    """
    Extract text from a material's file once and persist it (M4 Phase C).

    Returns the text, or "" if the file is unreadable or gone. Never raises:
    this runs inside an upload and inside a question, and neither should fail
    because a PDF is malformed or its file has been swept away by a deploy.

    Called from two places on purpose — at upload, while the file is
    guaranteed to exist, and lazily on first question for anything uploaded
    before this shipped. One function so the two paths cannot diverge in what
    they consider "extracted".
    """
    try:
        text = extract_text_from_file(material.file.path) or ""
    except Exception:
        logger.exception("Text extraction failed for material=%s", material.pk)
        return ""

    if text:
        # update_fields so a concurrent edit to title or group is not
        # clobbered by writing the whole row back.
        material.extracted_text = text
        material.save(update_fields=["extracted_text"])
    return text


class MaterialViewSet(viewsets.ModelViewSet):
    serializer_class   = StudyMaterialSerializer
    permission_classes = [IsAuthenticated]
    pagination_class   = LargePagination
    parser_classes     = (parsers.MultiPartParser, parsers.FormParser)
    filter_backends    = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields   = ['study_group', 'uploaded_by']
    search_fields      = ['title', 'study_group__name']

    def get_queryset(self):
        """
        Scoped to the caller's groups (M4 security fix).

        This replaced `queryset = StudyMaterial.objects.all()`, which was not
        merely a metadata leak: DRF resolves list, retrieve, update AND
        destroy through this method, so an unscoped queryset let any
        authenticated user read, rename and DELETE any other user's material.
        Measured before the fix — PATCH returned 200 with the title changed,
        DELETE returned 204 and the row was gone.

        Same helper and same model as RAGDoubtView, and the same shape as
        StudyGroupViewSet.get_queryset — no new permission system. Ordering is
        preserved from the attribute this replaces, so list responses are
        unchanged for anyone who was entitled to see them.
        """
        # select_related is load-bearing now that a page holds 50 rows instead
        # of 3: StudyMaterialSerializer nests `uploaded_by` and `study_group`,
        # so without it each row costs two extra queries. Measured on a
        # 50-row page: 103 queries before, 5 after (P2.1).
        #
        # '-id' breaks ties: upload_date is auto_now_add, so a batch upload
        # sorts arbitrarily and could shuffle between pages (P2.1).
        return accessible_materials(self.request.user).select_related(
            'uploaded_by', 'study_group'
        ).order_by('-upload_date', '-id')

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        A short-lived signed URL for one material (M5 Phase 3).

        `self.get_object()` runs against `get_queryset()`, which is
        `accessible_materials(...)` — so a non-member gets 404 here for the
        same reason they get 404 from retrieve, and no separate
        authorization path exists to drift out of sync.

        Returns the URL rather than redirecting: the SPA fetches this with
        its bearer token, and a 302 to a foreign origin inside an XHR would
        be blocked by the frontend's `connect-src` policy. The client
        assigns the returned URL to window.location, which CSP does not
        govern.
        """
        material = self.get_object()
        url = signed_url(material.file, filename=f"{material.title}")
        if not url:
            # The row survived a deploy but its bytes did not — the
            # ephemeral-filesystem case this whole phase exists to end.
            return Response(
                {"error": "This file is no longer available."}, status=410
            )
        return Response({"url": url, "expires_in": settings.SIGNED_URL_TTL})

    def perform_create(self, serializer):
        group_id = self.request.data.get('study_group')
        logger.info("Uploading material to group=%s", group_id)

        # The caller must belong to the group they upload into. `study_group`
        # is read_only on the serializer, so this id was passed straight
        # through as `study_group_id` with nothing validating it — measured
        # before this check: a non-member POSTed a file into another user's
        # group and got 201, with the row landing in that group.
        #
        # 400 rather than 403: a group the caller cannot see is not
        # meaningfully different from one that does not exist, and a bad id
        # was already a client error (an unknown id raised IntegrityError
        # from the FK, i.e. a 500 — this turns that into a 400 too).
        group = resolve_group(self.request.user, group_id)
        if group is None:
            raise ValidationError({'study_group': 'Invalid study group.'})

        # 1. Save normally to the File Library
        material = serializer.save(uploaded_by=self.request.user, study_group=group)
        file_name = material.file.name.lower()

        # Extract text NOW, while the file is guaranteed to exist. Render's
        # filesystem is ephemeral, so this is the only reliable window —
        # after the next deploy the file is gone and RAG would have nothing
        # to read. Best-effort: a malformed PDF must not fail the upload.
        if file_name.endswith(('.pdf', '.docx', '.txt')):
            cache_extracted_text(material)

        # 2. THE PIPELINE: Prepare images for MobileNetV2
        images_to_index = []

        if file_name.endswith(('.png', '.jpg', '.jpeg')):
            # It's a direct image, just add it to the queue
            images_to_index.append((material.file, material.title))
        
        elif file_name.endswith(('.pdf', '.docx')):
            # It's a document, rip the images out of it. The helper is
            # best-effort and returns [] on any failure, but the call is
            # guarded too: a stubbed or future version that raises must not
            # cost the user their upload.
            try:
                extracted_images = extract_images_from_document(material.file, file_name)
            except Exception:
                logger.exception("Image extraction raised for material=%s", material.pk)
                extracted_images = []
            for idx, img_bytes in enumerate(extracted_images):
                # Give each extracted diagram a unique name (e.g., "Chapter 3 - Diagram 1")
                images_to_index.append((img_bytes, f"{material.title} - Diagram {idx + 1}"))

        # 3. INDEXING: Pass everything we found through MobileNetV2
        for img_data, img_title in images_to_index:
            try:
                logger.info("Auto-indexing %r for semantic search", img_title)
                vector = VectorSearchService.extract_vector(img_data)

                # Save the extracted bytes as a real .jpg so the browser can render it
                img_data.seek(0)
                safe_filename = img_title.replace(" ", "_").replace("/", "_") + ".jpg"
                actual_image_file = ContentFile(img_data.read(), name=safe_filename)

                Document.objects.create(
                    group=material.study_group,
                    uploaded_by=self.request.user,
                    title=img_title,
                    file=actual_image_file,
                    file_type='image',
                    # `vector`, not json.dumps(vector). feature_vector is a
                    # pgvector VectorField and rejects a JSON string with
                    # ValueError("could not convert string to float") — which
                    # the bare `except` below then swallowed. Every diagram
                    # extracted from an uploaded PDF or DOCX silently failed
                    # to index. VisualSearchUploadView always passed the list
                    # correctly, which is why explicit uploads worked and
                    # this path did not.
                    feature_vector=vector,
                )
            except Exception:
                # Still non-fatal — one malformed diagram must not fail the
                # whole upload — but no longer silent. This ran for months
                # printing to stdout, where nothing was watching; the defect
                # above survived precisely because the failure was invisible.
                logger.exception("Auto-indexing failed for %r", img_title)

        logger.info("Upload pipeline complete for material=%s", material.pk)


# ─────────────────────────────────────────────────────────────
# AI Features
# ─────────────────────────────────────────────────────────────

class AIDoubtView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        material_id = request.data.get('materialId')
        question    = request.data.get('question')
        try:
            # The most direct bypass of the RAG fix: this is the same
            # "ask a question about a document" feature without retrieval, so
            # an unscoped lookup here handed back any user's document content
            # in the answer. Measured: 200, content returned.
            material = accessible_materials(request.user).get(id=material_id)
        except StudyMaterial.DoesNotExist:
            return Response({"error": "File not found"}, status=404)

        file_path = material.file.path
        extension = os.path.splitext(file_path)[1].lower()

        if extension in ['.jpg', '.jpeg', '.png']:
            answer = AIService.explain_image(load_image_for_ai(file_path), question)
        else:
            answer = AIService.get_answer(question, extract_text_from_file(file_path))

        return Response({"answer": answer}, status=200)


class AIQuizView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        material_id = request.data.get('materialId')
        try:
            # Same scoping as RAGDoubtView; a quiz generated from another
            # user's document leaks that document's content through the
            # questions and answers.
            material = accessible_materials(request.user).get(id=material_id)
        except StudyMaterial.DoesNotExist:
            return Response({"error": "File not found"}, status=404)

        file_path      = os.path.join(settings.MEDIA_ROOT, material.file.name)
        extracted_text = extract_text_from_file(file_path)
        if not extracted_text:
            return Response({"error": "Document is empty or unreadable"}, status=400)

        quiz = AIService.generate_quiz(extracted_text, num_questions=5)
        return Response({"quiz": quiz}, status=200)


# ─────────────────────────────────────────────────────────────
# Module B: Visual Semantic Search
# ─────────────────────────────────────────────────────────────

class VisualSearchUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request):
        group_id   = request.data.get('group_id')
        title      = request.data.get('title', 'Untitled')
        image_file = request.FILES.get('image')

        if not image_file:
            return Response({"error": "No image uploaded"}, status=400)
        if not group_id:
            return Response({"error": "group_id is required"}, status=400)

        # Membership check, same predicate and same 404-for-both as the query
        # view below. Unscoped, this let any authenticated user index an image
        # into any group — the write half of the same gap, and it would put
        # attacker-controlled documents into a victim's search results.
        group = resolve_group(request.user, group_id)
        if group is None:
            return Response({"error": "Group not found"}, status=404)

        print(f"🖼️ Extracting MobileNetV2 vector for: {title}")
        vector = VectorSearchService.extract_vector(image_file)

        doc = Document.objects.create(
            group=group,
            uploaded_by=request.user,
            title=title,
            file=image_file,
            file_type='image',
            feature_vector=vector,
        )
        return Response({
            "message":          "Image uploaded and indexed successfully!",
            "document_id":      doc.id,
            "vector_dimensions": len(vector),
        }, status=201)


from common.throttling import ClientIPUserRateThrottle, ClientIPAnonRateThrottle

class VisualSearchQueryView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPUserRateThrottle, ClientIPAnonRateThrottle]
    parser_classes     = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request):
        group_id    = request.data.get('group_id')
        query_image = request.FILES.get('image')

        if not query_image:
            return Response({"error": "No query image uploaded"}, status=400)

        # Previously an absent group_id fell through to
        # `Document.objects.filter(file_type='image')` — every image in the
        # system, belonging to every user. There is no "search everything"
        # scope for a group-scoped feature, so this is a client error rather
        # than a silently narrowed search. Matches VisualSearchUploadView,
        # which already required group_id.
        if not group_id:
            return Response({"error": "group_id is required"}, status=400)

        # Authorization BEFORE the expensive work: extract_vector runs a CLIP
        # forward pass, so checking access first also stops an unauthorized
        # caller from burning inference CPU on a box with 0.1 vCPU.
        #
        # Same predicate as StudyGroupViewSet and accessible_materials. 404
        # for both "no such group" and "not your group" — a 403 on the latter
        # would confirm the group exists and turn this into an id-enumeration
        # oracle, which is the whole reason the material endpoints use 404.
        group = resolve_group(request.user, group_id)
        if group is None:
            return Response({"error": "Group not found"}, status=404)

        try:
            top_k = int(request.data.get('top_k', 5))
        except (TypeError, ValueError):
            return Response({"error": "top_k must be an integer"}, status=400)
        top_k = max(1, min(top_k, 50))   # was an unguarded int(); 'abc' was a 500

        query_vector = VectorSearchService.extract_vector(query_image)
        documents    = Document.objects.filter(group=group, file_type='image')
        results = VectorSearchService.find_similar(query_vector, documents, top_k=top_k)

        return Response({
            "query_results": [
                {
                    "document_id":      doc.id,
                    "title":            doc.title,
                    "similarity_score": round(score, 4),
                    # Thumbnails restored (M5 Phase 3). M4 removed `file_url`
                    # because MEDIA was unauthenticated, so any URL here was a
                    # permanent unrevocable handle to the bytes. It is now a
                    # signed URL that expires in minutes, minted only after
                    # `group` has already been resolved through
                    # accessible_groups — every document in this list is one
                    # the caller may see.
                    "thumbnail_url":    signed_url(doc.file),
                    "uploaded_by":      doc.uploaded_by.username if doc.uploaded_by else "Unknown",
                    "uploaded_at":      doc.uploaded_at,
                }
                for score, doc in results
            ],
            "total_found": len(results)
        })


# ─────────────────────────────────────────────────────────────
# Module B (upgraded): RAG Doubt Solver
# ─────────────────────────────────────────────────────────────

class RAGDoubtView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPUserRateThrottle, ClientIPAnonRateThrottle]

    def post(self, request):
        material_id = request.data.get('materialId')
        question    = request.data.get('question')

        if not question:
            return Response({"error": "Question is required"}, status=400)

        try:
            # Scoped to what the caller may read. Unscoped, any authenticated
            # user could pass any material id and have its full text sent to
            # the LLM and returned to them — proven in
            # common/test_rag_authorization.py before this line existed.
            #
            # 404 rather than 403, deliberately: a 403 confirms the material
            # exists, turning the endpoint into an id-enumeration oracle. An
            # inaccessible material is now indistinguishable from a
            # nonexistent one, and the status code is unchanged from before.
            material = accessible_materials(request.user).get(id=material_id)
        except StudyMaterial.DoesNotExist:
            return Response({"error": "Material not found"}, status=404)

        extension = os.path.splitext(material.file.name)[1].lower()

        if extension in ['.jpg', '.jpeg', '.png']:
            # Vision path still needs the image bytes; nothing to cache.
            answer = AIService.explain_image(load_image_for_ai(material.file.path), question)
            return Response({"answer": answer, "mode": "vision"})

        raw_text = material.extracted_text
        if not raw_text:
            # Uploaded before Phase C, or extraction failed at upload. Read
            # the file and cache the result so this is paid at most once
            # more — measured at 398 ms against 0.5 ms for chunking, so it
            # is essentially the entire cost of preparing a RAG request.
            raw_text = cache_extracted_text(material)

        if not raw_text or len(raw_text) < 50:
            return Response({"error": "Could not extract text from document"}, status=400)

        chunks = RecursiveCharacterTextSplitter(
            chunk_size=500, chunk_overlap=50
        ).split_text(raw_text)

        result = RAGService.answer_with_rag(question, chunks)
        return Response({
            "answer": result.get("answer"),
            "citations": result.get("citations"),
            "mode": "rag",
            "chunks_searched": len(chunks)
        })


# ─────────────────────────────────────────────────────────────
# Module D: Hybrid AI Router
# ─────────────────────────────────────────────────────────────

class HybridRouterView(APIView):
    """
    THE TRAFFIC COP.
    POST { "subject": "Data Structures", "mastered_topics": ["Arrays"] }
    POST { "subject": "Tech Trivia", "elo_rating": 1250, "question_difficulty": 1300, "got_correct": true }
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPUserRateThrottle, ClientIPAnonRateThrottle]

    def post(self, request):
        serializer = HybridRouterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clean = serializer.validated_data
        
        subject = clean['subject']
        
        from .models import UserCodingProfile
        # SRS FR-HRCH-01: shared mastery definition (accuracy >= 0.8).
        mastered = get_mastered_topic_names(request.user)

        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)

        user_data = {
            "mastered_topics":     mastered,
            "elo_rating":          profile.elo_rating,
            "question_difficulty": clean.get('question_difficulty'),
            "got_correct":         clean.get('got_correct'),
            "user":                request.user,
        }
        return Response(route_recommendation(subject, user_data))


class MasteryMapView(APIView):
    """
    Returns full prerequisite graph with mastery status per node.
    GET  /api/ai/mastery-map/?subject=DSA   (mastered topics derived from DB;
                                             returns the map at the top level,
                                             as LearningPathVisualizer expects)
    POST { "subject": "DSA", "mastered_topics": ["Variables", "Arrays"] }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.core.exceptions import ValidationError

        subject  = request.query_params.get('subject', 'DSA')
        # SRS FR-HRCH-01: shared mastery definition (accuracy >= 0.8).
        mastered = get_mastered_topic_names(request.user)
        try:
            mastery_map = HierarchicalEngine.get_mastery_map(subject, mastered)
        except ValidationError:
            mastery_map = {}

        # Enrich with the user's real per-topic accuracy plus the M7
        # effective-mastery signals (skill x predicted retention, §6.2).
        from django.utils import timezone as dj_tz
        from learning.memory import effective_mastery, is_due, retention
        from .models import UserTopicMastery

        now = dj_tz.now()
        state = {
            name: (acc, hl, last, reviews)
            for name, acc, hl, last, reviews in UserTopicMastery.objects.filter(
                user=request.user
            ).values_list('topic__name', 'accuracy', 'hlr_halflife', 'last_practiced', 'reviews')
        }
        for name, node in mastery_map.items():
            acc, hl, last, reviews = state.get(name, (0.0, 1.0, None, 0))
            days = max((now - last).total_seconds() / 86400.0, 0.0) if last else 0.0
            r = retention(hl, days) if reviews else 1.0
            node['accuracy_pct'] = round(acc * 100, 1)
            node['effective_mastery_pct'] = round(effective_mastery(acc, r) * 100, 1)
            node['due'] = is_due(reviews, r)

        return Response(mastery_map)

    def post(self, request):
        subject  = request.data.get('subject', 'DSA')
        mastered = request.data.get('mastered_topics', [])
        mastery_map = HierarchicalEngine.get_mastery_map(subject, mastered)
        return Response({"mastery_map": mastery_map, "subject": subject})


# ─────────────────────────────────────────────────────────────
# WebSocket: Group Message History (REST fallback)
# ─────────────────────────────────────────────────────────────

class GroupMessageHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        # Membership was already enforced here, but as 403-if-not-a-member
        # and 404-if-absent — which told an attacker exactly which group ids
        # exist. One 404 for both (M4 WP2), matching every other
        # group-scoped endpoint.
        group = resolve_group(request.user, group_id)
        if group is None:
            return Response({"error": "Group not found"}, status=404)

        messages = GroupMessage.objects.filter(
            group=group
        ).select_related('sender').order_by('-timestamp')[:50]

        data = [
            {
                "id":        m.id,
                "content":   m.content,
                "username":  m.sender.username,
                "user_id":   m.sender.id,
                "timestamp": m.timestamp.strftime("%H:%M"),
                "date":      m.timestamp.strftime("%d %b %Y"),
            }
            for m in reversed(list(messages))
        ]
        return Response({"messages": data, "count": len(data)})


# ─────────────────────────────────────────────────────────────
# Quiz & Analytics
# ─────────────────────────────────────────────────────────────

class QuizResultCreateView(generics.CreateAPIView):
    queryset           = QuizResult.objects.all()
    serializer_class   = QuizResultSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def analytics_data(request):
    user       = request.user
    raw_scores = QuizResult.objects.filter(user=user).values_list('score', flat=True)
    score_distribution = [
        {"range": "0-40%",   "count": 0},
        {"range": "40-60%",  "count": 0},
        {"range": "60-80%",  "count": 0},
        {"range": "80-100%", "count": 0},
    ]
    for s in raw_scores:
        if s < 40:   score_distribution[0]["count"] += 1
        elif s < 60: score_distribution[1]["count"] += 1
        elif s < 80: score_distribution[2]["count"] += 1
        else:        score_distribution[3]["count"] += 1

    subjects       = UserActivity.objects.filter(user=user).values_list('section_name', flat=True).distinct()
    bivariate_data = []
    for subject in subjects:
        activity      = UserActivity.objects.filter(user=user, section_name=subject).aggregate(Sum('time_spent'))
        total_seconds = activity['time_spent__sum'].total_seconds() if activity['time_spent__sum'] else 0
        avg_score     = QuizResult.objects.filter(user=user, topic__icontains=subject).aggregate(Avg('score'))
        bivariate_data.append({
            "subject":       subject,
            "hours_studied": round(total_seconds / 3600, 1),
            "average_score": avg_score['score__avg'] or 0,
        })

    return Response({"univariate": score_distribution, "bivariate": bivariate_data})


# ─────────────────────────────────────────────────────────────
# Assigned Quizzes
# ─────────────────────────────────────────────────────────────

class AssignedQuizCreateView(generics.CreateAPIView):
    # No `queryset` attribute: CreateAPIView never reads one (POST only),
    # and leaving `AssignedQuiz.objects.all()` sitting here is the exact
    # shape that made MaterialViewSet and ManageAssignedQuizView leak the
    # moment either grew a read method.
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """
        Found by the sprint's own sweep, not by the audit (M4 WP2).

        The group was resolved with an unscoped `StudyGroup.objects.get(...)`
        — the same defect class as everywhere else. It was saved only by the
        creator check below, and that check raised AttributeError:
        `permissions.PermissionDenied` does not exist (it lives in
        rest_framework.exceptions), as does `serializer.ValidationError`.
        Both therefore returned 500 rather than 403/400. They failed CLOSED,
        so this was never exploitable — but an authorization decision that
        depends on an exception firing from a typo is not a control.
        """
        group = resolve_group(self.request.user, self.request.data.get('study_group'))
        if group is None:
            raise ValidationError({'study_group': 'Invalid study group.'})
        if self.request.user != group.creator:
            raise PermissionDenied("Only the group creator can assign quizzes")
        serializer.save(assigned_by=self.request.user, study_group=group)


class ListAssignedQuizView(generics.ListAPIView):
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]
    pagination_class   = LargePagination

    def get_queryset(self):
        """
        Scoped to the caller's groups (M4 WP2).

        Was `AssignedQuiz.objects.filter(study_group_id=group_id)` on a
        client-supplied id with no membership check. AssignedQuizSerializer
        exposes `quiz_data`, which holds the answer key — so this handed any
        authenticated user the answers to any group's quiz. Measured: 200,
        with the answers in the body.

        The `study_group` filter is preserved exactly; it now narrows within
        what the caller may reach instead of selecting from everything.
        """
        group_id = self.request.query_params.get('study_group')
        if not group_id:
            return AssignedQuiz.objects.none()
        group = resolve_group(self.request.user, group_id)
        if group is None:
            return AssignedQuiz.objects.none()
        # 'id' breaks ties: deadline is a client-supplied field, and a creator
        # setting several quizzes to the same due date is ordinary use. Tied
        # rows sort arbitrarily, so without this a quiz can appear on two pages
        # while another appears on none (P2.1).
        # select_related: AssignedQuizSerializer exposes `creator_name` from
        # assigned_by.username, one extra query per row without it (P2.1).
        return AssignedQuiz.objects.filter(study_group=group).select_related(
            'assigned_by'
        ).order_by('deadline', 'id')


class ManageAssignedQuizView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scoped to the caller's groups (M4 WP2).

        Was `queryset = AssignedQuiz.objects.all()`. Update and destroy were
        guarded by the creator checks below, but RETRIEVE was not guarded at
        all — the same shape as the original MaterialViewSet defect. Any
        authenticated user could GET any quiz by id and read its answers.

        Scoping here also hardens update/destroy: a non-member now gets 404
        rather than 403, so the endpoint no longer confirms that an id
        exists. The creator checks stay — they enforce a stricter rule
        (owner-only writes) than membership.
        """
        return AssignedQuiz.objects.filter(
            study_group__in=accessible_groups(self.request.user)
        )

    def perform_update(self, serializer):
        if self.get_object().study_group.creator != self.request.user:
            raise PermissionDenied("Only the Group Owner can edit this quiz!")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.study_group.creator != self.request.user:
            raise PermissionDenied("Only the Group Owner can delete this quiz!")
        instance.delete()


# ─────────────────────────────────────────────────────────────
# Group Members
# ─────────────────────────────────────────────────────────────

class getGroupMembers(generics.ListAPIView):
    # UserDisplaySerializer, not UserSerializer (M4 WP3). The roster of a
    # group is the one place a user's record is shown to OTHER people, and
    # UserSerializer carries `email` — so this endpoint published every
    # member's address to anyone who could reach it. Nothing in the UI reads
    # the email from here; it renders username, university and role.
    serializer_class   = UserDisplaySerializer
    permission_classes = [IsAuthenticated]
    pagination_class   = LargePagination

    def get_queryset(self):
        """
        Scoped to groups the caller belongs to (M4 WP2).

        Was `StudyGroup.objects.get(id=group_id)` with no membership check,
        so any authenticated user could enumerate any group's full roster —
        measured, with email addresses in the payload.

        An empty queryset for both "no such group" and "not your group":
        distinguishing them would report count=1 vs count=0 and make this a
        group-existence oracle.
        """
        group = resolve_group(self.request.user, self.kwargs['group_id'])
        if group is None:
            return User.objects.none()
        # order_by is required, not cosmetic: an unordered queryset makes
        # Postgres free to return rows in any order per query, so the same
        # member could appear on page 1 and page 2 while another never appears
        # at all. DRF warns about exactly this (UnorderedObjectListWarning).
        # `id` is insertion order — the closest stable order to what the
        # unordered query already returned in practice (M2 P2.1).
        return User.objects.filter(
            Q(id__in=group.members.all()) | Q(id=group.creator_id)
        ).distinct().order_by('id')


# ─────────────────────────────────────────────────────────────
# Social: Friends & Connections
# ─────────────────────────────────────────────────────────────

class UserSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get('q', '')
        if len(query) < 3:
            return Response({"users": []})
        users = User.objects.filter(username__icontains=query).exclude(id=request.user.id)[:10]
        return Response({"users": UserBasicSerializer(users, many=True).data})


class FriendRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        receiver_id = request.data.get('receiver_id')
        try:
            receiver = User.objects.get(id=receiver_id)
        except User.DoesNotExist:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        if (Connection.objects.filter(sender=request.user, receiver=receiver).exists() or
                Connection.objects.filter(sender=receiver, receiver=request.user).exists()):
            return Response({"error": "Connection already exists or is pending."}, status=400)

        Connection.objects.create(sender=request.user, receiver=receiver, status='pending')
        return Response({"message": "Friend request sent!"}, status=status.HTTP_201_CREATED)


class FriendRequestActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, connection_id):
        action_type = request.data.get('action')
        try:
            connection = Connection.objects.get(id=connection_id, receiver=request.user, status='pending')
        except Connection.DoesNotExist:
            return Response({"error": "Request not found."}, status=status.HTTP_404_NOT_FOUND)

        if action_type == 'accept':
            connection.status = 'accepted'
            connection.save()
            return Response({"message": "Friend request accepted!"})
        elif action_type == 'reject':
            connection.status = 'rejected'
            connection.save()
            return Response({"message": "Friend request rejected."})
        return Response({"error": "Invalid action."}, status=400)

    def delete(self, request, connection_id):
        try:
            connection = Connection.objects.get(id=connection_id)
            if request.user not in [connection.sender, connection.receiver]:
                return Response({"error": "Unauthorized"}, status=403)
            connection.delete()
            return Response({"message": "Friend removed."})
        except Connection.DoesNotExist:
            return Response({"error": "Request not found."}, status=404)

class FriendsListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        pending   = Connection.objects.filter(receiver=request.user, status='pending')
        accepted  = Connection.objects.filter(
            Q(sender=request.user) | Q(receiver=request.user), status='accepted'
        )
        return Response({
            "pending": ConnectionSerializer(pending, many=True).data,
            "friends": ConnectionSerializer(accepted, many=True).data,
        })


# ─────────────────────────────────────────────────────────────
# Legacy
# ─────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def process_document(request):
    # Was the one endpoint that had already fallen through the missing
    # DEFAULT_PERMISSION_CLASSES (M4 WP0): unauthenticated callers could
    # POST a PDF and have PyPDF2 parse it on a 512 MB / 0.1 vCPU instance.
    # Explicit even though the new default would cover it — this endpoint
    # is the reason the default exists.
    uploaded_file = request.FILES.get('document')
    if not uploaded_file:
        return Response({"error": "No document uploaded!"}, status=400)
    try:
        reader   = PyPDF2.PdfReader(uploaded_file)
        raw_text = "".join(page.extract_text() for page in reader.pages)
        if not raw_text.strip():
            return Response({"error": "Could not extract text from this PDF."}, status=400)
        chunks = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50).split_text(raw_text)
        return Response({
            "status":              "success",
            "total_chunks":        len(chunks),
            "preview_first_chunk": chunks[0] if chunks else ""
        })
    except Exception as e:
        return Response({"status": "error", "message": str(e)}, status=500)

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def update_user_activity(request):
    """Stub endpoint for user activity tracking"""
    return Response({"status": "success"})

class HealthCheckView(APIView):
    """
    Public health check endpoint for Datadog / Load Balancers.
    Pings the DB to ensure full connectivity.
    """
    # Explicit AllowAny rather than an empty list (M4 WP0). `[]` meant "no
    # permission checks", which read identically to "nobody set this" — the
    # ambiguity the default-deny baseline exists to remove.
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        try:
            # Simple query to verify DB is alive
            from django.contrib.auth import get_user_model
            get_user_model().objects.exists()
            return Response({"status": "ok", "db": "connected"}, status=200)
        except Exception:
            # Static payload (M4 WP5). This returned str(e) to an
            # unauthenticated caller, which on a connection failure is the
            # database host, port and user. Detail goes to the log, where
            # the operator can read it; the public body says only "down".
            logger.exception("Health check failed")
            return Response({"status": "error", "db": "disconnected"}, status=503)