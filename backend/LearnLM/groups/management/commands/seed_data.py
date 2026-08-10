import csv
import os
from django.core.management.base import BaseCommand
from common.environment import require_disposable_environment
from groups.models import Topic, Question

class Command(BaseCommand):
    help = (
        'DESTRUCTIVE. Deletes EVERY Question and reseeds from the LeetCode CSV. '
        'Question deletion cascades into CodeSubmission and AgenticCoachLog, so '
        'this destroys learner submission history. Refused unless SPARKLM_ENV is '
        'a disposable environment AND --wipe-all-questions is passed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--wipe-all-questions', action='store_true',
            help='Acknowledge that every Question — and every submission that '
                 'references one — will be deleted.',
        )

    def handle(self, *args, **kwargs):
        # Gate BEFORE any work: this used to delete unconditionally (M2 P2.5).
        require_disposable_environment(
            'seed_data (wipes all questions)',
            acknowledged=kwargs.get('wipe_all_questions', False),
        )
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        csv_path = os.path.join(base_dir, 'data', 'LeetCode_Questions_updated (2024-11-02).csv')

        if not os.path.exists(csv_path):
            self.stdout.write(self.style.ERROR(f'❌ CSV file not found at {csv_path}'))
            return

        self.stdout.write('🧹 Wiping old placeholder questions from the database...')
        Question.objects.all().delete() # Clears the bad data

        self.stdout.write('🚀 Starting fresh data ingestion...')
        
        # Extended list of hierarchical tags based on your dataset
        hierarchical_tags = ['Array', 'String', 'Hash Table', 'Dynamic Programming', 'Math', 'Sorting', 'Binary Search', 'Two Pointers', 'Linked List']

        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            
            count = 0
            for row in reader:
                # 1. Grab Title directly from the 'Question' column
                title = row.get('Question', '').strip()
                if not title:
                    continue

                # 2. Handle missing descriptions by providing the URL
                link = row.get('Question_Link', '')
                description = f"Problem description not provided in dataset.\n\nPlease view the full problem here:\n{link}"

                # 3. Process Difficulty
                difficulty_str = row.get('Difficulty', 'Medium').strip()
                elo_map = {'Easy': 1000, 'Medium': 1200, 'Hard': 1500}
                base_elo = elo_map.get(difficulty_str, 1200)

                # 4. Clean up the messy list-string format from the CSV
                raw_tags = row.get('Topic_tags', '')
                clean_tags = raw_tags.replace('[', '').replace(']', '').replace("'", "").replace('"', "")
                main_tag = clean_tags.split(',')[0].strip() if clean_tags else 'General'
                if not main_tag:
                    main_tag = 'General'
                
                # 5. The Traffic Cop Logic
                structure = 'hierarchical' if main_tag in hierarchical_tags else 'flat'

                topic, _ = Topic.objects.get_or_create(
                    name=main_tag,
                    defaults={'structure_type': structure}
                )

                # Create the fresh question
                Question.objects.create(
                    title=title,
                    topic=topic,
                    content=description,
                    base_difficulty=base_elo
                )
                
                count += 1
                if count % 100 == 0:
                    self.stdout.write(f'✅ Processed {count} questions...')
                
                # Stop at 500 for testing limits
                if count >= 500: 
                    break

        self.stdout.write(self.style.SUCCESS('🎉 Successfully re-seeded the database with real titles!'))