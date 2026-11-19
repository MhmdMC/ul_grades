import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import g

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
test_dir = tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH'] = str(Path(test_dir.name) / 'test.sqlite3')
os.environ['DISABLE_BACKGROUND_SERVICES'] = '1'
os.environ['SECRET_KEY'] = 'semester-tests-only'
m = importlib.import_module('app')
m.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
m.limiter.enabled = False


@pytest.fixture(autouse=True)
def database():
    with m.app.app_context():
        m.db.drop_all()
        m.db.create_all()
        for table in ('grades', 'grade_averages', 'public_shares'):
            m.db.session.execute(m.text(f'DROP TABLE IF EXISTS {table}'))
        for cid in ('old', 'new'):
            m.db.session.add(m.SchoolClass(class_id=cid, name=f'Semester {cid}', language='english'))
        user = m.User(username='student', password_hash='unused', ul_student_id='1234', ul_class_id='new')
        viewer = m.User(username='viewer', password_hash='unused')
        m.db.session.add_all([user, viewer])
        m.db.session.commit()
        yield user, viewer
        m.db.session.remove()


def snapshot(mark):
    return m.normalize_snapshot({'grades': [{'materialId': 'repeat', 'partial': {'value': mark, 'rank': 2}}],
                                 'average': {'partialAverage': mark, 'partialRank': 2}})


def login(client, user):
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True


@pytest.fixture(scope='session', autouse=True)
def close_database():
    yield
    with m.app.app_context():
        m.db.session.remove()
        m.db.engine.dispose()
    test_dir.cleanup()


def test_semester_storage_and_deletion_are_isolated(database):
    user, _ = database
    m.persist_snapshot(user, snapshot(65), 'old')
    m.persist_snapshot(user, snapshot(90), 'new')
    assert m.stored_snapshot(user)['courses'][0]['partial'] == 90
    assert m.stored_snapshot(user, 'old')['courses'][0]['partial'] == 65
    m.persist_snapshot(user, m.normalize_snapshot({'grades': []}), 'new')
    assert m.stored_snapshot(user)['courses'] == []
    assert m.stored_snapshot(user, 'old')['courses'][0]['partial'] == 65


def test_sharing_profiles_and_rankings_are_isolated(database):
    user, viewer = database
    m.persist_snapshot(user, snapshot(65), 'old')
    m.persist_snapshot(user, snapshot(90), 'new')
    keys = m.coupled_share_keys({'course:repeat:partial'})
    m.set_public_keys(user, keys, 'old')
    assert m.public_keys_for(user) == set()
    old = m.viewable_profile(user, viewer, 'old')
    new = m.viewable_profile(user, viewer, 'new')
    assert any(f['value'] == 65 for f in old['courses'][0]['fields'])
    assert all(f['value'] is None for f in new['courses'][0]['fields'])
    with m.app.test_request_context():
        assert m.build_leaderboard(['old'], 'course:repeat:partial', viewer)[0]['name'] == 'student'
        current = m.build_leaderboard(['new'], 'course:repeat:partial', viewer)[0]
        assert current['name'] == 'Anonymous'
        assert current['value'] == 90


def test_routes_default_current_and_reject_other_enrollment(database):
    user, viewer = database
    m.persist_snapshot(user, snapshot(65), 'old')
    m.persist_snapshot(user, snapshot(90), 'new')
    client = m.app.test_client()
    login(client, user)
    assert client.get('/api/dashboard').json['average'] == 90
    assert client.get('/api/dashboard?semester=old').json['average'] == 65
    assert client.get('/dashboard?semester=someone-else').status_code == 404
    for route in ['/dashboard', '/dashboard?semester=old', '/share?semester=old', '/u/student?semester=old', '/leaderboard?class_id=old', '/public', '/search?q=1234']:
        assert client.get(route).status_code == 200, route
    response = client.post('/share?semester=old', data={'public': 'course:repeat:partial'})
    assert response.status_code == 302
    assert m.public_keys_for(user) == set()
    assert 'course:repeat:partial' in m.public_keys_for(user, 'old')
    assert b'semester=old' in client.get('/public').data
    login(client, viewer)
    assert b'90.0' not in client.get('/u/student').data


def test_migration_is_idempotent_and_preserves_legacy(database):
    user, _ = database
    m.db.session.execute(m.text('CREATE TABLE grades (user_id INTEGER, material_id TEXT, partial FLOAT, partial_rank INTEGER, final FLOAT, final_grade FLOAT, final_rank INTEGER, updated_at DATETIME)'))
    m.db.session.execute(m.text("INSERT INTO grades VALUES (:uid, 'repeat', 72, 2, NULL, NULL, NULL, '2026-01-01 00:00:00')"), {'uid': user.id})
    m.db.session.execute(m.text('CREATE TABLE public_shares (user_id INTEGER, item_key TEXT)'))
    m.db.session.execute(m.text("INSERT INTO public_shares VALUES (:uid, 'course:repeat:partial')"), {'uid': user.id})
    m.db.session.commit()
    m.migrate_semester_data()
    m.migrate_semester_data()
    assert m.Grade.query.count() == 1
    assert m.stored_snapshot(user)['courses'][0]['partial'] == 72
    assert m.public_keys_for(user) == {'course:repeat:partial'}
    assert m.db.session.execute(m.text('SELECT count(*) FROM grades')).scalar() == 1


def test_sync_failure_keeps_saved_grades_and_current_identity(database, monkeypatch):
    user, _ = database
    user.ul_cookie = 'test-cookie'
    m.persist_snapshot(user, snapshot(65), 'old')
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: SimpleNamespace(status_code=503, json_data=None))
    assert not m.sync_semester(user, 'old')
    assert m.stored_snapshot(user, 'old')['average'] == 65
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: SimpleNamespace(status_code=200, json_data=snapshot(70)['raw']))
    assert m.sync_semester(user, 'old')
    assert user.ul_class_id == 'new'
    assert m.stored_snapshot(user, 'old')['average'] == 70


def test_identity_discovers_all_classes_and_uses_current_endpoint(database, monkeypatch):
    user, _ = database
    user.ul_cookie = 'test-cookie'
    user.ul_class_id = 'old'
    user.last_snapshot = json.dumps({**snapshot(65)['raw'], '_requested_class_id': 'old'})
    response = lambda data: SimpleNamespace(status_code=200, json_data=data)
    monkeypatch.setattr(m.ul_api, 'get_me', lambda *a: response({'username': '1234', 'name': 'Student'}))
    # Deliberately unordered: current endpoint must win over list position.
    monkeypatch.setattr(m.ul_api, 'get_student_classes', lambda *a: response({'data': [{'class': {'id': 'new'}}, {'class': {'id': 'old'}}]}))
    monkeypatch.setattr(m.ul_api, 'get_current_class', lambda *a: response({'classId': 'new'}))
    m.ensure_ul_identity_from_cookie(user, force=True)
    assert user.ul_class_id == 'new'
    assert {row.class_id for row in user.semesters} == {'new', 'old'}
    assert m.stored_snapshot(user, 'old')['average'] == 65
    assert user.last_snapshot is None


def test_account_delete_removes_all_semesters(database):
    user, _ = database
    for cid in ('old', 'new'):
        m.persist_snapshot(user, snapshot(65), cid)
        m.set_public_keys(user, {'overall:partial_average'}, cid)
    m.db.session.delete(user)
    m.db.session.commit()
    assert m.Grade.query.count() == 0
    assert m.GradeAverage.query.count() == 0
    assert m.PublicShare.query.count() == 0
    assert m.StudentSemester.query.count() == 0


def test_admin_consent_is_preserved_for_history(database):
    user, viewer = database
    viewer.is_admin = True
    m.persist_snapshot(user, snapshot(65), 'old')
    profile = m.viewable_profile(user, viewer, 'old')
    assert profile['visible_count'] == 0
    user.hostage_consent = True
    profile = m.viewable_profile(user, viewer, 'old')
    assert profile['visible_count'] == profile['total_count']


def test_unexpected_api_json_does_not_erase_history(database, monkeypatch):
    user, _ = database
    user.ul_cookie = 'test-cookie'
    m.persist_snapshot(user, snapshot(65), 'old')
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: SimpleNamespace(status_code=200, json_data={'error': 'unavailable'}))
    assert not m.sync_semester(user, 'old')
    assert m.stored_snapshot(user, 'old')['average'] == 65


def test_refresh_all_imports_history_without_sharing(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'test-cookie'
    for cid in ('old', 'new'):
        m.db.session.add(m.StudentSemester(user_id=user.id, class_id=cid))
    m.db.session.commit()
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: True)
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda uid, cid, cookie: SimpleNamespace(status_code=200, json_data=snapshot(65 if cid == 'old' else 90)['raw']))
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: fn(*args))
    client = m.app.test_client()
    login(client, admin)
    assert client.post('/admin/semesters/sync').status_code == 302
    m.db.session.expire_all()
    assert m.stored_snapshot(user, 'old')['average'] == 65
    assert m.stored_snapshot(user) is None
    assert m.PublicShare.query.count() == 0
    job = m.SemesterSyncJob.query.one()
    assert job.completed == 1 and job.failed == 0 and job.status == 'completed'
    assert client.get('/admin/semesters').status_code == 200


def test_nested_major_from_ulfg_is_a_text_label(database):
    user, _ = database
    user.ul_cookie = 'test-cookie'
    record = m.ensure_class_record('old', user, payload={
        'id': 'old', 'year': 2025, 'language': 'english',
        'definition': {'year': 3, 'half': 'fall', 'major': {'id': 7, 'name': 'Electrical Engineering'}},
        'branch': {'number': 3, 'name': 'Branch 3'},
    })
    assert record.major == 'Electrical Engineering'
    assert record.academic_year == '2025'
    assert 'Electrical Engineering' in record.name
    assert '{' not in record.name


def test_students_cannot_import_history_even_by_posting_directly(database, monkeypatch):
    user, _ = database
    client = m.app.test_client()
    login(client, user)
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda *a: pytest.fail('student started import'))
    assert client.post('/semesters/sync').status_code == 403
    assert client.post('/admin/semesters/sync').status_code == 403
    assert client.get('/admin/semesters').status_code == 403
    assert m.SemesterSyncJob.query.count() == 0


def test_dashboard_discovery_does_not_fetch_history(database, monkeypatch):
    user, _ = database
    user.ul_cookie = 'test-cookie'
    m.persist_snapshot(user, snapshot(90), 'new')
    m.db.session.add(m.StudentSemester(user_id=user.id, class_id='old'))
    m.db.session.commit()
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: True)
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: pytest.fail('dashboard fetched history'))
    client = m.app.test_client()
    login(client, user)
    for route in ('/dashboard', '/dashboard?semester=old'):
        page = client.get(route)
        assert page.status_code == 200
        assert b'Refresh all semesters' not in page.data
    assert m.stored_snapshot(user, 'old') is None


def test_admin_single_semester_and_duplicate_job_guard(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'test-cookie'
    m.persist_snapshot(user, snapshot(65), 'old')
    m.persist_snapshot(user, snapshot(90), 'new')
    m.set_public_keys(user, {'overall:partial_average'}, 'old')
    calls = []
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: calls.append(args))
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: pytest.fail('single-semester refresh rediscovered classes'))
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda uid, cid, cookie: SimpleNamespace(status_code=200, json_data=snapshot(70)['raw']))
    client = m.app.test_client()
    login(client, admin)
    assert client.post('/admin/semesters/sync', data={'user_id': user.id, 'class_id': 'new'}).status_code == 404
    assert client.post('/admin/semesters/sync', data={'user_id': user.id, 'class_id': 'other'}).status_code == 404
    assert client.post('/admin/semesters/sync', data={'user_id': user.id, 'class_id': 'old'}).status_code == 302
    assert client.post('/admin/semesters/sync').status_code == 302
    assert len(calls) == 1
    m.run_semester_sync_job(calls[0][0])
    m.db.session.expire_all()
    assert m.stored_snapshot(user, 'old')['average'] == 70
    assert m.stored_snapshot(user)['average'] == 90
    assert m.public_keys_for(user, 'old') == {'overall:partial_average'}
    assert user.hostage_consent is False


def test_admin_refresh_reports_safe_failure_and_continues(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'test-cookie'
    m.persist_snapshot(user, snapshot(65), 'old')
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: True)
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: (_ for _ in ()).throw(RuntimeError('SECRET_MUST_NOT_BE_SAVED')))
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: fn(*args))
    client = m.app.test_client()
    login(client, admin)
    assert client.post('/admin/semesters/sync', data={'user_id': user.id}).status_code == 302
    m.db.session.expire_all()
    job = m.SemesterSyncJob.query.one()
    assert job.failed == 1 and job.active_key is None
    assert 'SECRET_MUST_NOT_BE_SAVED' not in job.details
    assert m.stored_snapshot(user, 'old')['average'] == 65


def test_admin_student_scope_does_not_fetch_other_students(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'test-cookie'
    other = m.User(username='other', password_hash='unused', ul_student_id='5678', ul_class_id='new', ul_cookie='other-cookie')
    m.db.session.add(other)
    m.db.session.commit()
    for student in (user, other):
        m.persist_snapshot(student, snapshot(65), 'old')
    discovered = []
    fetched = []
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda student, **kw: discovered.append(student.id))
    def fetch(uid, cid, cookie):
        fetched.append(uid)
        return SimpleNamespace(status_code=200, json_data=snapshot(70)['raw'])
    monkeypatch.setattr(m.ul_api, 'get_grades', fetch)
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: fn(*args))
    client = m.app.test_client()
    login(client, admin)
    client.post('/admin/semesters/sync', data={'user_id': user.id})
    assert discovered == [user.id]
    assert fetched == ['1234']
    m.db.session.expire_all()
    assert m.stored_snapshot(other, 'old')['average'] == 65


def test_stalled_history_job_can_be_retried(database, monkeypatch):
    _, admin = database
    admin.is_admin = True
    stalled = m.SemesterSyncJob(active_key='history', status='running', updated_at=m.now_utc()-m.timedelta(minutes=11))
    m.db.session.add(stalled)
    m.db.session.commit()
    started = []
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: started.append(args))
    client = m.app.test_client()
    login(client, admin)
    assert b'stopped reporting progress' in client.get('/admin/semesters').data
    client.post('/admin/semesters/sync')
    m.db.session.refresh(stalled)
    assert stalled.status == 'interrupted' and stalled.active_key is None
    assert len(started) == 1


def test_history_import_requires_csrf(database, monkeypatch):
    _, admin = database
    admin.is_admin = True
    m.db.session.commit()
    monkeypatch.setitem(m.app.config, 'WTF_CSRF_ENABLED', True)
    client = m.app.test_client()
    login(client, admin)
    assert client.post('/admin/semesters/sync').status_code == 400
    assert m.SemesterSyncJob.query.count() == 0


def test_latest_semester_does_not_depend_on_api_list_order():
    entries = [
        {'class': {'id': 'current', 'year': 2026, 'definition': {'year': 3, 'half': 'fall'}}},
        {'class': {'id': 'spring', 'year': 2025, 'definition': {'year': 2, 'half': 'spring'}}},
        {'class': {'id': 'fall', 'year': 2025, 'definition': {'year': 2, 'half': 'fall'}}},
    ]
    assert m.latest_class_id_from_payload({'data': entries}) == 'current'
    assert m.latest_class_id_from_payload(list(reversed(entries))) == 'current'
    assert m.latest_class_id_from_payload(entries[1:]) == 'spring'


def test_leaderboard_newest_year_first_and_admin_default(database):
    user, admin = database
    admin.is_admin = True
    for cid, year, name in [('old', '2025', 'A old class'), ('new', '2026', 'Z new class')]:
        school_class = m.db.session.get(m.SchoolClass, cid)
        school_class.academic_year = year
        school_class.name = name
        m.persist_snapshot(user, snapshot(65), cid)
    client = m.app.test_client()
    login(client, admin)
    page = client.get('/leaderboard').data.decode()
    assert page.index('value="2026"') < page.index('value="2025"')
    assert 'Z new class' in page and 'A old class' not in page
    assert 'name="class_id" value="new"' in page
    old_page = client.get('/leaderboard?class_id=old').data.decode()
    assert 'name="class_id" value="old"' in old_page


def setup_repeaters(database):
    repeater, regular = database
    for cid, year in [('old', '2025'), ('new', '2026')]:
        record = m.db.session.get(m.SchoolClass, cid)
        record.year = 1
        record.half = 'fall'
        record.academic_year = year
    regular.ul_class_id = 'new'
    regular.ul_student_id = '5678'
    regular.ul_cookie = 'regular-cookie'
    repeater.ul_cookie = 'repeat-cookie'
    group = m.Group(name='Current group', class_id='new')
    m.db.session.add(group)
    m.db.session.commit()
    for user in (repeater, regular):
        user.group_id = group.id
        m.persist_snapshot(user, two_courses(60, 80), 'new')
        user.last_snapshot = json.dumps(two_courses(60, 80)['raw'])
    m.persist_snapshot(repeater, two_courses(60, 80), 'old')
    group.representative_user_id = repeater.id
    group.last_json = json.dumps(two_courses(60, 80)['raw'], sort_keys=True)
    m.db.session.commit()
    return repeater, regular, group


def two_courses(first, second):
    return m.normalize_snapshot({'grades': [
        {'materialId': 'one', 'partial': first}, {'materialId': 'two', 'partial': second}],
        'average': {'partialAverage': 70}})


def api_response(payload, status=200):
    return SimpleNamespace(status_code=status, json_data=payload, endpoint='/test', response_time=0.01)


def test_repeated_attempts_excluded_only_on_leaderboard(database):
    user, viewer, _ = setup_repeaters(database)
    assert m.is_repeated_semester(user, 'new')
    assert not m.is_repeated_semester(user, 'old')
    assert not m.is_repeated_semester(viewer, 'new')
    with m.app.test_request_context():
        assert len(m.build_leaderboard(['new'], 'course:one:partial', user)) == 1
        assert len(m.build_leaderboard(['old'], 'course:one:partial', user)) == 1
    assert m.dashboard_context(user)['courses'][0]['partial'] == 60
    assert m.viewable_profile(user, user, 'new')['visible_count'] > 0
    m.set_public_keys(user, {'course:one:partial'}, 'new')
    assert m.viewable_profile(user, viewer, 'new')['courses'][0]['fields'][0]['value'] == 60


def test_representative_preference_is_retroactive_and_resets_baseline(database):
    repeater, regular, group = setup_repeaters(database)
    assert m.choose_new_representative(group).id == regular.id
    assert group.representative_user_id == regular.id and group.last_json is None
    regular.ul_cookie = None
    assert m.choose_new_representative(group).id == repeater.id
    regular.ul_cookie = 'back'
    assert m.choose_new_representative(group).id == regular.id


def test_rep_update_unlocks_only_changed_course_and_retries_failure(database, monkeypatch):
    repeater, regular, group = setup_repeaters(database)
    m.choose_new_representative(group)
    group.last_json = json.dumps(two_courses(60, 80)['raw'], sort_keys=True)
    m.db.session.commit()
    state = {'fail': True}
    def request(user):
        if user.id == repeater.id and state['fail']:
            return api_response(None, 503)
        return api_response(two_courses(61 if user.id == regular.id else 60, 80)['raw'])
    monkeypatch.setattr(m, 'api_request', request)
    m.poll_group(group)
    one = m.Grade.query.filter_by(user_id=repeater.id, class_id='new', material_id='one').one()
    assert one.leaderboard_confirmed_at is None
    assert m.CourseRelease.query.count() == 1
    state['fail'] = False
    m.poll_group(group)  # Representative payload is unchanged; failed members still retry.
    m.db.session.refresh(one)
    assert one.leaderboard_confirmed_at is not None
    two = m.Grade.query.filter_by(user_id=repeater.id, class_id='new', material_id='two').one()
    assert two.leaderboard_confirmed_at is None
    with m.app.test_request_context():
        assert len(m.build_leaderboard(['new'], 'course:one:partial', regular)) == 2
        assert len(m.build_leaderboard(['new'], 'course:two:partial', regular)) == 1
        assert len(m.build_leaderboard(['new'], 'overall:partial_average', regular)) == 1
    monkeypatch.setattr(m, 'api_request', lambda user: api_response(two_courses(61, 81)['raw']))
    m.poll_group(group)
    with m.app.test_request_context():
        assert len(m.build_leaderboard(['new'], 'course:two:partial', regular)) == 2
        assert len(m.build_leaderboard(['new'], 'overall:partial_average', regular)) == 2


def test_representative_switch_and_rank_only_changes_do_not_release(database, monkeypatch):
    _, regular, group = setup_repeaters(database)
    raw = two_courses(70, 90)['raw']
    monkeypatch.setattr(m, 'api_request', lambda user: api_response(raw))
    m.poll_group(group)  # Switching from repeater to regular establishes a new baseline.
    assert group.representative_user_id == regular.id
    assert m.CourseRelease.query.count() == 0
    raw = two_courses(70, 90)['raw']
    raw['grades'][0]['partial'] = {'value': 70, 'rank': 3}
    m.poll_group(group)
    assert m.CourseRelease.query.count() == 0


def test_leaderboard_personal_scope_empty_classes_and_year_filter(database):
    user, other = database
    other.ul_class_id = 'outside'
    for cid, year in [('old', '2025'), ('new', '2026'), ('outside', '2026'), ('empty', '2026')]:
        row = m.db.session.get(m.SchoolClass, cid)
        if row is None:
            row = m.SchoolClass(class_id=cid, name=f'Class {cid}')
            m.db.session.add(row)
        row.academic_year = year
    m.persist_snapshot(user, snapshot(0), 'new')
    m.persist_snapshot(user, snapshot(65), 'old')
    m.persist_snapshot(other, snapshot(80), 'outside')
    m.persist_snapshot(user, snapshot(None), 'empty')
    client = m.app.test_client()
    login(client, user)
    page = client.get('/leaderboard').data.decode()
    assert 'Class outside' not in page and 'Class empty' not in page
    assert 'name="class_id" value="new"' in page
    assert 'Semester old' not in page
    all_page = client.get('/leaderboard?scope=all&year=2026').data.decode()
    assert 'Class outside' in all_page and 'Class empty' not in all_page
    old_page = client.get('/leaderboard?year=2025').data.decode()
    assert 'Semester old' in old_page and 'Semester new' not in old_page


def test_new_non_repeater_replaces_representative_on_join(database, monkeypatch):
    repeater, regular, group = setup_repeaters(database)
    regular.group_id = None
    m.db.session.commit()
    assert m.choose_new_representative(group).id == repeater.id
    monkeypatch.setattr(m.ul_api, 'get_me', lambda *a: api_response({'username': '5678'}))
    monkeypatch.setattr(m.ul_api, 'get_student_classes', lambda *a: api_response([
        {'class': {'id': 'new', 'year': 2026, 'language': 'english',
                   'definition': {'year': 1, 'half': 'fall'}}}]))
    monkeypatch.setattr(m.ul_api, 'get_current_class', lambda *a: api_response({'classId': 'new'}))
    m.ensure_ul_identity_from_cookie(regular, force=True)
    assert group.representative_user_id == regular.id


def test_third_attempt_is_a_repeat_and_ordinary_refresh_does_not_unlock(database):
    user, _, _ = setup_repeaters(database)
    m.db.session.add(m.SchoolClass(class_id='third', name='Third', academic_year='2027', year=1, half='fall'))
    m.persist_snapshot(user, two_courses(75, 85), 'third')
    assert m.is_repeated_semester(user, 'third')
    assert not m.leaderboard_metric_allowed(user, 'third', 'course:one:partial')
    m.persist_snapshot(user, two_courses(76, 85), 'third')
    assert not m.leaderboard_metric_allowed(user, 'third', 'course:one:partial')


def test_bad_rep_payload_cannot_create_a_false_release(database, monkeypatch):
    _, _, group = setup_repeaters(database)
    m.choose_new_representative(group)
    original = json.dumps(two_courses(60, 80)['raw'], sort_keys=True)
    group.last_json = original
    m.db.session.commit()
    monkeypatch.setattr(m, 'api_request', lambda user: api_response({'data': {'error': 'temporary'}}))
    m.poll_group(group)
    assert group.last_json == original
    monkeypatch.setattr(m, 'api_request', lambda user: api_response(two_courses(60, 80)['raw']))
    m.poll_group(group)
    assert m.CourseRelease.query.count() == 0


def test_poll_response_does_not_follow_user_into_a_new_semester(database, monkeypatch):
    user, _ = database
    user.ul_cookie = 'cookie'
    user.ul_class_id = 'old'
    m.persist_snapshot(user, snapshot(65), 'old')
    def delayed_response(student):
        # Enrollment discovery finishes while the old-class HTTP request is in flight.
        student.ul_class_id = 'new'
        student.last_snapshot = None
        m.db.session.commit()
        return api_response(snapshot(66)['raw'])
    monkeypatch.setattr(m, 'api_request', delayed_response)
    m.fetch_and_compare_user(user)
    assert m.stored_snapshot(user, 'new') is None
    assert user.last_snapshot is None


def test_unlabelled_cached_grades_are_not_backfilled_under_current_class(database):
    user, _ = database
    user.last_snapshot = json.dumps(snapshot(65)['raw'])
    m.db.session.commit()
    m.backfill_stored_grades()
    assert m.stored_snapshot(user) is None
    assert m.dashboard_context(user)['courses'] == []


def test_response_with_wrong_class_id_is_rejected(database):
    user, _ = database
    raw = {**snapshot(65)['raw'], 'classId': 'old'}
    with pytest.raises(m.ULIdentityBootstrapError):
        m.persist_snapshot(user, m.normalize_snapshot(raw), 'new')
    assert m.stored_snapshot(user) is None


def test_admin_current_refresh_replaces_corrupt_copy_and_preserves_history(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'cookie'
    for cid in ('old', 'new'):
        m.persist_snapshot(user, snapshot(65), cid)
        m.set_public_keys(user, {'overall:partial_average'}, cid)
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: True)
    calls = []
    def fetch(uid, cid, cookie):
        calls.append(cid)
        return api_response({'grades': [{'materialId': 'new-course', 'partial': None}]})
    monkeypatch.setattr(m.ul_api, 'get_grades', fetch)
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: fn(*args))
    client = m.app.test_client()
    login(client, admin)
    assert client.post('/admin/semesters/sync', data={'scope': 'current'}).status_code == 302
    m.db.session.expire_all()
    assert calls == ['new']
    assert m.stored_snapshot(user)['courses'][0]['key'] == 'new-course'
    assert m.stored_snapshot(user)['average'] is None
    assert m.stored_snapshot(user, 'old')['average'] == 65
    assert m.public_keys_for(user) == {'overall:partial_average'}
    assert m.public_keys_for(user, 'old') == {'overall:partial_average'}
    assert m.matching_cached_snapshot(user)['_requested_class_id'] == 'new'
    job = m.SemesterSyncJob.query.one()
    assert job.scope == 'current' and job.completed == 1 and job.failed == 0
    assert b'Refresh all current semesters' in client.get('/admin/semesters').data


def test_current_refresh_failure_preserves_saved_data_and_is_admin_only(database, monkeypatch):
    user, admin = database
    admin.is_admin = True
    user.ul_cookie = 'cookie'
    m.persist_snapshot(user, snapshot(65), 'new')
    monkeypatch.setattr(m, 'ensure_ul_identity_from_cookie', lambda *a, **kw: True)
    monkeypatch.setattr(m.ul_api, 'get_grades', lambda *a: api_response(None, 503))
    monkeypatch.setattr(m.socketio, 'start_background_task', lambda fn, *args: fn(*args))
    client = m.app.test_client()
    login(client, user)
    assert client.post('/admin/semesters/sync', data={'scope': 'current'}).status_code == 403
    login(client, admin)
    client.post('/admin/semesters/sync', data={'scope': 'current'})
    m.db.session.expire_all()
    assert m.stored_snapshot(user)['average'] == 65
    assert m.SemesterSyncJob.query.one().failed == 1
