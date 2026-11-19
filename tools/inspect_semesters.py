"""Read-only semester diagnostics. Does not import the app or print credentials/marks."""
import argparse
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('username')
parser.add_argument('--database', default=str(Path(__file__).resolve().parents[1] / 'ul_grades.sqlite3'))
args = parser.parse_args()
connection = sqlite3.connect(Path(args.database).resolve().as_uri() + '?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
user = connection.execute('SELECT id,ul_class_id,last_snapshot FROM users WHERE username=? OR ul_student_id=?',
                          (args.username, args.username)).fetchone()
if user is None:
    raise SystemExit('Account not found.')
print('Current class ID:', user['ul_class_id'])
payload = json.loads(user['last_snapshot'] or '{}')
if isinstance(payload, dict):
    print('Cached snapshot class:', payload.get('_requested_class_id') or payload.get('class_id') or payload.get('classId') or 'unrecorded')
semesters = connection.execute('''SELECT s.class_id,c.name,s.synced_at FROM student_semesters s
    LEFT JOIN classes c ON c.class_id=s.class_id WHERE s.user_id=?''', (user['id'],)).fetchall()
records = {}
for semester in semesters:
    grades = connection.execute('''SELECT material_id,partial,final,final_grade,updated_at
        FROM semester_grades WHERE user_id=? AND class_id=?''', (user['id'], semester['class_id'])).fetchall()
    records[semester['class_id']] = {row['material_id']: tuple(row)[1:4] for row in grades}
    print('Semester:', semester['class_id'], semester['name'], '| courses:', len(grades), '| synced:', semester['synced_at'])
current = records.get(user['ul_class_id'], {})
for cid, grades in records.items():
    if cid == user['ul_class_id']:
        continue
    shared = current.keys() & grades.keys()
    equal = sum(current[key] == grades[key] and any(value is not None for value in current[key]) for key in shared)
    print('Current vs', cid, '| overlapping course IDs:', len(shared), '| identical nonempty marks:', equal)
connection.close()
