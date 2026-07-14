# ul_grades

Flask app. A grade monitor for students at the Lebanese University
Faculty of Engineering.

## Stack
- Flask + Jinja templates (`templates/`)
- Tailwind (currently via CDN) + `static/css/app.css`
- SQLite (`ul_grades.sqlite3`)
- Socket.IO for live grade updates

## Design
All visual work MUST follow DESIGN.md. Read it before editing anything in
`static/` or `templates/`. The anti-pattern list in §1 is binding, not
advisory. Do not introduce gradients, box-shadows, or decorative animation.

## Working style
- One step at a time. Do not restyle multiple screens in a single pass.
- Show the diff before applying.