# Generic school integration fixtures

`tests/fixtures/scholarium/demo-course.json` supplies two fictitious organizations and teachers, a course, and two immutable lesson versions. All names/content are synthetic. The file confers no authority, contains no student data and requires no school login.

The three policy expectations describe future tests, not current authorization results. Fixture tests currently verify references, separation and revision consistency only. Actual school-side rights resolution, revocation, version-bound teacher approval, scoped outgoing worker authorization and private video storage remain to be implemented and tested separately.

Existing school integration code belongs to the separate Scholarium repository under its own license. No such source code was copied here. The current local studio still requires its own visible human approval; a school fixture cannot replace it. Studio Copilot support does not authorize Copilot for classroom use.
