"""Import tasks from a filesystem project dir into a server project, keeping ids, timestamps, authors, comments."""
import os, sys, uuid
from pathlib import Path
from oloop import store as st
from oloop.client import ServerApiError
from oloop.credentials import AgentCredentials
from oloop.crypto import doc_aad, seal_envelope
from oloop.server_store import ServerStore

src, proj_name, mode = Path(sys.argv[1]).expanduser(), sys.argv[2], sys.argv[3]
creds = AgentCredentials.load(os.environ["OLOOP_AGENT_CREDENTIALS"])
store = ServerStore.from_credentials(creds)
project = store.get_project(proj_name)
existing = {t.id for t in store.list_tasks(status=None)} if False else set(store._tasks)

tasks = []
for p in sorted((src / "tasks").glob("*.md")):
    data, body = st._parse(p.read_text(encoding="utf-8"), str(p))
    st._validate(st._task_validator, data, str(p))
    known, extra = st._split(data, st._TASK_FIELDS)
    comments = [st.Comment(extra=ce, **ck) for ck, ce in (st._split(c, st._COMMENT_FIELDS) for c in known.pop("comments", []))]
    if known.get("dueDate") is None:
        known.pop("dueDate", None)
    known["projectId"] = project.id
    t = st.Task(description=body, comments=comments, projectName=project.name, extra=extra, **known)
    st._validate(st._task_validator, t.frontmatter(), t.id)
    tasks.append(t)

dup = [t.id for t in tasks if t.id in existing]
print(f"{len(tasks)} tasks parsed, {sum(len(t.comments) for t in tasks)} comments, {len(dup)} already on server")
if mode != "run":
    sys.exit(0)

for t in tasks:
    if t.id in existing:
        continue
    kv, key = store._get_latest_project_key(project.id)
    env = seal_envelope(key, os.urandom(24), doc_aad("task", t.id, kv, 1),
                        st._render(t.frontmatter(), t.description).encode("utf-8"))
    res = store.client.put_document(doc_id=t.id, kind="task", key_version=kv, rev=1, data=env,
                                    project_id=project.id, base_rev=None)
    print("ok", t.id[:8], t.status, len(t.comments))
