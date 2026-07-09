import os
import json
import io
import zipfile
import fitz  # PyMuPDF
import PyPDF2

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
from rest_framework.exceptions import PermissionDenied

from django_filters.rest_framework import DjangoFilterBackend
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ── Single clean import block — no duplicates ────────────────
from .ai_services import AIService, VectorSearchService, RAGService
from .hybrid_router import route_recommendation, HierarchicalEngine
from .utils import extract_text_from_file, load_image_for_ai
from .models import (
    StudyGroup, StudyMaterial, QuizResult, UserActivity,
    AssignedQuiz, Connection, DirectMessage, Document, GroupMessage
)
from .serializers import (
    ConnectionSerializer, QuizResultSerializer, StudyGroupSerializer,
    UserBasicSerializer, UserSerializer, StudyMaterialSerializer, AssignedQuizSerializer,
    HybridRouterSerializer
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────

class LargePagination(PageNumberPagination):
    page_size = 8
    page_size_query_param = 'page_size'
    max_page_size = 1000


# ─────────────────────────────────────────────────────────────
# Auth & User
# ─────────────────────────────────────────────────────────────

class CreateUserView(generics.CreateAPIView):
    serializer_class = UserSerializer
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
            "achievement_points": 100
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
        return StudyGroup.objects.filter(
            Q(members=user) | Q(creator=user)
        ).distinct().order_by('-created_at')

    def perform_create(self, serializer):
        group = serializer.save(creator=self.request.user)
        group.members.add(self.request.user)

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
    """
    images = []
    file_bytes = file_obj.read()
    file_obj.seek(0)  # CRITICAL: Reset pointer so Django can still save the original file
    
    lower_name = filename.lower()
    
    try:
        if lower_name.endswith('.pdf'):
            print(f"📄 Scanning PDF: {filename} for diagrams...")
            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(pdf_doc)):
                page = pdf_doc.load_page(page_num)
                for img in page.get_images(full=True):
                    xref = img[0]
                    base_image = pdf_doc.extract_image(xref)
                    images.append(io.BytesIO(base_image["image"]))
                    
        elif lower_name.endswith('.docx'):
            print(f"📝 Unzipping DOCX: {filename} for diagrams...")
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx_zip:
                for item in docx_zip.namelist():
                    # Microsoft Word secretly stores all images in this internal folder
                    if item.startswith('word/media/') and item.lower().endswith(('.png', '.jpeg', '.jpg')):
                        images.append(io.BytesIO(docx_zip.read(item)))
    except Exception as e:
        print(f"⚠️ Extraction warning for {filename}: {e}")
        
    print(f"🔍 Found {len(images)} images hidden inside {filename}")
    return images


class MaterialViewSet(viewsets.ModelViewSet):
    queryset           = StudyMaterial.objects.all().order_by('-upload_date')
    serializer_class   = StudyMaterialSerializer
    permission_classes = [IsAuthenticated]
    parser_classes     = (parsers.MultiPartParser, parsers.FormParser)
    filter_backends    = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields   = ['study_group', 'uploaded_by']
    search_fields      = ['title', 'study_group__name']

    def perform_create(self, serializer):
        group_id = self.request.data.get('study_group')
        print(f"📦 Uploading file to library... Group ID: {group_id}")
        
        # 1. Save normally to the File Library
        material = serializer.save(uploaded_by=self.request.user, study_group_id=group_id)
        file_name = material.file.name.lower()

        # 2. THE PIPELINE: Prepare images for MobileNetV2
        images_to_index = []

        if file_name.endswith(('.png', '.jpg', '.jpeg')):
            # It's a direct image, just add it to the queue
            images_to_index.append((material.file, material.title))
        
        elif file_name.endswith(('.pdf', '.docx')):
            # It's a document, rip the images out of it!
            extracted_images = extract_images_from_document(material.file, file_name)
            for idx, img_bytes in enumerate(extracted_images):
                # Give each extracted diagram a unique name (e.g., "Chapter 3 - Diagram 1")
                images_to_index.append((img_bytes, f"{material.title} - Diagram {idx + 1}"))

        # 3. INDEXING: Pass everything we found through MobileNetV2
        for img_data, img_title in images_to_index:
            try:
                print(f"🤖 Auto-indexing '{img_title}' for AI Semantic Search...")
                vector = VectorSearchService.extract_vector(img_data)
                
                # 👈 THE FIX: We must save the extracted bytes as a real .jpg file so the browser can render it!
                img_data.seek(0)
                safe_filename = img_title.replace(" ", "_").replace("/", "_") + ".jpg"
                actual_image_file = ContentFile(img_data.read(), name=safe_filename)
                
                Document.objects.create(
                    group=material.study_group,
                    uploaded_by=self.request.user,
                    title=img_title,
                    file=actual_image_file,  # Link to the newly created JPG file!
                    file_type='image',
                    feature_vector=json.dumps(vector),
                )
            except Exception as e:
                print(f"❌ AI Indexing failed for {img_title}: {e}")
                
        print("✅ Pipeline execution complete!")


# ─────────────────────────────────────────────────────────────
# AI Features
# ─────────────────────────────────────────────────────────────

class AIFlashcardView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        material_id = request.data.get('materialId')
        topic       = request.data.get('topic', 'General')
        try:
            material = StudyMaterial.objects.get(id=material_id)
        except StudyMaterial.DoesNotExist:
            return Response({"error": "File not found"}, status=404)

        extracted_text = extract_text_from_file(material.file.path)
        if not extracted_text:
            return Response({"error": "PDF is empty or unreadable"}, status=400)

        flashcards = AIService.generate_flashcards(extracted_text, num_cards=10)
        return Response({"flashcards": flashcards}, status=200)


class AIDoubtView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        material_id = request.data.get('materialId')
        question    = request.data.get('question')
        try:
            material = StudyMaterial.objects.get(id=material_id)
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
            material = StudyMaterial.objects.get(id=material_id)
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

        try:
            group = StudyGroup.objects.get(id=group_id)
        except StudyGroup.DoesNotExist:
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


from rest_framework.throttling import UserRateThrottle, AnonRateThrottle

class VisualSearchQueryView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, AnonRateThrottle]
    parser_classes     = (parsers.MultiPartParser, parsers.FormParser)

    def post(self, request):
        group_id    = request.data.get('group_id')
        query_image = request.FILES.get('image')
        top_k       = int(request.data.get('top_k', 5))

        if not query_image:
            return Response({"error": "No query image uploaded"}, status=400)

        query_vector = VectorSearchService.extract_vector(query_image)
        documents    = (
            Document.objects.filter(group_id=group_id, file_type='image')
            if group_id else
            Document.objects.filter(file_type='image')
        )
        results = VectorSearchService.find_similar(query_vector, documents, top_k=top_k)

        return Response({
            "query_results": [
                {
                    "document_id":      doc.id,
                    "title":            doc.title,
                    "similarity_score": round(score, 4),
                    "file_url":         doc.file.url if doc.file else doc.file_url,
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
    throttle_classes = [UserRateThrottle, AnonRateThrottle]

    def post(self, request):
        material_id = request.data.get('materialId')
        question    = request.data.get('question')

        if not question:
            return Response({"error": "Question is required"}, status=400)

        try:
            material = StudyMaterial.objects.get(id=material_id)
        except StudyMaterial.DoesNotExist:
            return Response({"error": "Material not found"}, status=404)

        file_path = material.file.path
        extension = os.path.splitext(file_path)[1].lower()

        if extension in ['.jpg', '.jpeg', '.png']:
            answer = AIService.explain_image(load_image_for_ai(file_path), question)
            return Response({"answer": answer, "mode": "vision"})

        raw_text = extract_text_from_file(file_path)
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
    throttle_classes = [UserRateThrottle, AnonRateThrottle]

    def post(self, request):
        serializer = HybridRouterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clean = serializer.validated_data
        
        subject = clean['subject']
        
        from .models import UserTopicMastery, UserCodingProfile
        mastered = list(UserTopicMastery.objects.filter(
            user=request.user, 
            accuracy__gte=0.8
        ).values_list('topic__name', flat=True))
        
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
        from .models import UserTopicMastery

        subject  = request.query_params.get('subject', 'DSA')
        mastered = list(UserTopicMastery.objects.filter(
            user=request.user, accuracy__gte=0.8
        ).values_list('topic__name', flat=True))
        try:
            mastery_map = HierarchicalEngine.get_mastery_map(subject, mastered)
        except ValidationError:
            mastery_map = {}
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
        try:
            group = StudyGroup.objects.get(id=group_id)
        except StudyGroup.DoesNotExist:
            return Response({"error": "Group not found"}, status=404)

        if not group.members.filter(id=request.user.id).exists() and group.creator != request.user:
            return Response({"error": "Not a member of this group"}, status=403)

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
    queryset           = AssignedQuiz.objects.all()
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        group_id = self.request.data.get('study_group')
        try:
            group = StudyGroup.objects.get(id=group_id)
        except StudyGroup.DoesNotExist:
            raise serializer.ValidationError("Study group not found")
        if self.request.user != group.creator:
            raise permissions.PermissionDenied("Only the group creator can assign quizzes")
        serializer.save(assigned_by=self.request.user)


class ListAssignedQuizView(generics.ListAPIView):
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        group_id = self.request.query_params.get('study_group')
        if group_id:
            return AssignedQuiz.objects.filter(study_group_id=group_id).order_by('deadline')
        return AssignedQuiz.objects.none()


class ManageAssignedQuizView(generics.RetrieveUpdateDestroyAPIView):
    queryset           = AssignedQuiz.objects.all()
    serializer_class   = AssignedQuizSerializer
    permission_classes = [IsAuthenticated]

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
    serializer_class   = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        group_id = self.kwargs['group_id']
        try:
            group = StudyGroup.objects.get(id=group_id)
            return User.objects.filter(
                Q(id__in=group.members.all()) | Q(id=group.creator.id)
            ).distinct()
        except StudyGroup.DoesNotExist:
            return User.objects.none()


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
def process_document(request):
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
    permission_classes = [] # Publicly accessible for automated checkers
    throttle_classes = []

    def get(self, request):
        try:
            # Simple query to verify DB is alive
            from django.contrib.auth import get_user_model
            get_user_model().objects.exists()
            return Response({"status": "ok", "db": "connected"}, status=200)
        except Exception as e:
            return Response({"status": "error", "db": "disconnected", "message": str(e)}, status=503)