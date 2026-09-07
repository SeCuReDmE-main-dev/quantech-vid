"""Fixture consistency only: these tests do not prove external authorization."""
import json
from pathlib import Path


def test_synthetic_school_fixture_has_consistent_isolated_entities():
    fixture = json.loads((Path(__file__).parent / "fixtures/scholarium/demo-course.json").read_text())
    assert fixture["synthetic"] is True and fixture["authority"] == "none"
    organizations = {value["id"] for value in fixture["organizations"]}
    teachers = {value["id"] for value in fixture["teachers"]}
    courses = {value["id"] for value in fixture["courses"]}
    assert len(organizations) == len(teachers) == 2
    for member in fixture["memberships"]:
        assert member["teacher_id"] in teachers and member["organization_id"] in organizations
    for course in fixture["courses"]:
        assert course["organization_id"] in organizations
    revisions = {(value["id"], value["revision"]) for value in fixture["lessons"]}
    assert len(revisions) == len(fixture["lessons"])
    assert all(value["course_id"] in courses for value in fixture["lessons"])
    assert all((key, value) in revisions for key, value in fixture["active_lesson_revisions"].items())
    assert len(fixture["scenarios"]) == 3
    assert all(value["teacher_id"] in teachers and (value["lesson_id"], value["revision"]) in revisions
               for value in fixture["scenarios"])
    serialized = json.dumps(fixture).lower()
    assert not any(key in serialized for key in ('password', 'cookie', 'email', 'student', 'biometric'))
