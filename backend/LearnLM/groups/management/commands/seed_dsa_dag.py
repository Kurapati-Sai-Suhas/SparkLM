"""
Seeds the DSA curriculum DAG over the EXISTING question-bank topics.

Rewritten during the taxonomy-alignment pass:
- The previous version created a parallel set of empty topics ("Arrays",
  "Trees", "Hashing", ...) alongside the LeetCode-tag topics that actually
  hold the questions ("Array", "Tree", "Hash Table", ...). Every DAG node
  without questions made the hierarchical router silently fall back to
  serving arbitrary problems. Curriculum nodes now reference the populated
  topic names directly.
- The previous version DELETED the "DSA Masterclass" portal before
  recreating it. Topic.portal and Question.topic both cascade, so once the
  real topics belong to the portal that delete would wipe every question
  in the bank. The portal is now get_or_create'd and never deleted.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups.models import CodingPortal, Topic, TopicPrerequisite

# Every node below is an existing question-bank topic (LeetCode tag) that
# already holds seeded questions, so the DAG can always serve topic-matched
# problems.
CURRICULUM_TOPICS = [
    "Array", "String", "Math", "Bit Manipulation",            # roots
    "Hash Table", "Two Pointers", "Stack", "Binary Search",
    "Linked List", "Greedy",
    "Tree", "Trie",
    "Backtracking",
    "Depth-First Search", "Breadth-First Search",
    "Graph", "Union Find",
    "Divide and Conquer",
    "Dynamic Programming",
]

# { "Topic": ["Prerequisite1", ...] } — must form a DAG.
PREREQUISITES_MAP = {
    "Hash Table":           ["Array"],
    "Two Pointers":         ["Array"],
    "Stack":                ["Array"],
    "Binary Search":        ["Array"],
    "Linked List":          ["Array"],
    "Greedy":               ["Array"],
    "Tree":                 ["Linked List"],
    "Trie":                 ["Tree", "String"],
    "Backtracking":         ["Tree"],
    "Depth-First Search":   ["Tree", "Stack"],
    "Breadth-First Search": ["Tree"],
    "Graph":                ["Depth-First Search", "Breadth-First Search"],
    "Union Find":           ["Graph"],
    "Divide and Conquer":   ["Binary Search"],
    "Dynamic Programming":  ["Array", "Backtracking"],
}

# Empty duplicate topics created by the previous seeder. Deleted ONLY when
# they still hold zero questions (cascade guard).
LEGACY_EMPTY_TOPICS = [
    "Arrays", "Strings", "Math & Geometry", "Hashing", "Sliding Window",
    "Linked Lists", "Queue", "Trees", "Tries", "Heaps", "Recursion",
    "Graphs", "Breadth-First Search (BFS)", "Depth-First Search (DFS)",
    "Advanced Graphs", "1D Dynamic Programming", "2D Dynamic Programming",
]


class Command(BaseCommand):
    help = 'Seeds the DSA curriculum DAG over the existing question-bank topics.'

    def handle(self, *args, **options):
        with transaction.atomic():
            dsa_portal, created = CodingPortal.objects.get_or_create(
                name="DSA Masterclass",
                defaults={
                    'description': "Comprehensive Data Structures and Algorithms curriculum for interview prep.",
                    'is_active': True,
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Reusing'} portal: {dsa_portal.name}"
            ))

            # Attach curriculum topics to the portal (reusing existing rows —
            # this is what connects the DAG to the real question bank).
            topic_objs = {}
            for name in CURRICULUM_TOPICS:
                topic, _ = Topic.objects.get_or_create(
                    name=name,
                    defaults={'portal': dsa_portal, 'structure_type': 'hierarchical'},
                )
                topic.portal = dsa_portal
                topic.structure_type = 'hierarchical'
                topic.save(update_fields=['portal', 'structure_type'])
                topic_objs[name] = topic

            self.stdout.write(self.style.SUCCESS(
                f"Linked {len(topic_objs)} curriculum topics to the portal."
            ))

            # Remove the old seeder's empty duplicates so the router and
            # mastery map stop seeing question-less ghost nodes.
            removed = 0
            for name in LEGACY_EMPTY_TOPICS:
                legacy = Topic.objects.filter(name=name).first()
                if legacy and not legacy.question_set.exists():
                    legacy.delete()
                    removed += 1
            if removed:
                self.stdout.write(self.style.WARNING(
                    f"Removed {removed} legacy empty duplicate topic(s)."
                ))

            # Rebuild the prerequisite edges for the curriculum topics.
            TopicPrerequisite.objects.filter(topic__in=topic_objs.values()).delete()

            links_created = 0
            for topic_name, prereqs in PREREQUISITES_MAP.items():
                for p_name in prereqs:
                    TopicPrerequisite.objects.create(
                        topic=topic_objs[topic_name],
                        prerequisite=topic_objs[p_name],
                    )
                    links_created += 1

            self.stdout.write(self.style.SUCCESS(
                f"Created {links_created} prerequisite edges. DSA Curriculum DAG seeded! 🚀"
            ))
