# backend/models/generated/

Agent-modified `.idf` files produced during runtime evaluation, one per run,
recorded against the run id.

Most control happens through runtime actuators and never touches a file. This
directory holds the cases where a policy implies a structural change to the
model rather than a set-point change — which is what the deliverable means by
"the modified versions generated during runtime evaluation".

Generated at runtime; contents are not committed.
