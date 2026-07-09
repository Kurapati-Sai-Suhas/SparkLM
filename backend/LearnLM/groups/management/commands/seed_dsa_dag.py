from django.core.management.base import BaseCommand
from groups.models import CodingPortal, Topic, TopicPrerequisite
from django.db import transaction

class Command(BaseCommand):
    help = 'Seeds a robust Data Structures and Algorithms curriculum DAG.'

    def handle(self, *args, **options):
        with transaction.atomic():
            self.stdout.write(self.style.WARNING("Clearing old DSA topics and portals..."))
            
            # Optional: Clean up existing DSA topics so we don't duplicate
            CodingPortal.objects.filter(name="DSA Masterclass").delete()
            
            self.stdout.write(self.style.SUCCESS("Creating DSA Masterclass portal..."))
            dsa_portal = CodingPortal.objects.create(
                name="DSA Masterclass",
                description="Comprehensive Data Structures and Algorithms curriculum for interview prep.",
                is_active=True
            )

            # Define the curriculum topics
            topics = [
                "Arrays", "Strings", "Math & Geometry", "Bit Manipulation", # Roots
                "Hashing", "Two Pointers", "Sliding Window", "Stack", "Binary Search", # Array dependencies
                "Linked Lists", "Queue",
                "Trees", "Tries", "Heaps",
                "Recursion", "Backtracking",
                "Graphs", "Breadth-First Search (BFS)", "Depth-First Search (DFS)", "Advanced Graphs",
                "Greedy", "1D Dynamic Programming", "2D Dynamic Programming"
            ]

            topic_objs = {}
            for t in topics:
                topic_objs[t], created = Topic.objects.get_or_create(
                    name=t,
                    defaults={
                        'portal': dsa_portal,
                        'structure_type': 'hierarchical'
                    }
                )
                if not created:
                    topic_objs[t].portal = dsa_portal
                    topic_objs[t].structure_type = 'hierarchical'
                    topic_objs[t].save()

            self.stdout.write(self.style.SUCCESS(f"Seeded {len(topic_objs)} topics."))

            # Define prerequisites: { "Topic": ["Prerequisite1", "Prerequisite2"] }
            # Must form a DAG!
            prerequisites_map = {
                "Hashing": ["Arrays"],
                "Two Pointers": ["Arrays"],
                "Sliding Window": ["Arrays", "Two Pointers"],
                "Stack": ["Arrays"],
                "Binary Search": ["Arrays"],
                "Linked Lists": ["Arrays"], # Usually taught after Arrays
                "Queue": ["Arrays", "Linked Lists"],
                "Trees": ["Linked Lists"],
                "Tries": ["Trees", "Strings"],
                "Heaps": ["Trees"],
                "Recursion": ["Trees"], # Recursion is usually taught with Trees
                "Backtracking": ["Recursion"],
                "Graphs": ["Trees"],
                "Breadth-First Search (BFS)": ["Graphs", "Queue"],
                "Depth-First Search (DFS)": ["Graphs", "Stack", "Recursion"],
                "Advanced Graphs": ["Graphs", "Breadth-First Search (BFS)", "Depth-First Search (DFS)"],
                "Greedy": ["Arrays"],
                "1D Dynamic Programming": ["Recursion", "Arrays"],
                "2D Dynamic Programming": ["1D Dynamic Programming"]
            }

            self.stdout.write(self.style.WARNING("Linking prerequisites to build the DAG..."))
            
            # Clear existing prereqs for these topics to avoid cycle exceptions during seeding
            for t_name, t_obj in topic_objs.items():
                TopicPrerequisite.objects.filter(topic=t_obj).delete()

            links_created = 0
            for topic_name, prereqs in prerequisites_map.items():
                target_topic = topic_objs[topic_name]
                for p_name in prereqs:
                    prereq_topic = topic_objs[p_name]
                    TopicPrerequisite.objects.create(
                        topic=target_topic,
                        prerequisite=prereq_topic
                    )
                    links_created += 1

            self.stdout.write(self.style.SUCCESS(f"Successfully created {links_created} prerequisite edges."))
            self.stdout.write(self.style.SUCCESS("DSA Curriculum DAG Seeded Successfully! 🚀"))
