import os
import sys
import django

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute('DELETE FROM groups_usertopicmastery;')
    cursor.execute('DELETE FROM groups_codesubmission;')
    cursor.execute('DELETE FROM groups_agenticcoachlog;')
    cursor.execute('DELETE FROM groups_recommendationlog;')

print("Cleared conflicting rows successfully!")
