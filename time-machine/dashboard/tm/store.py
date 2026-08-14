"""Content-addressed object store + manifests (SCAFFOLD).

Stores checkpoint content under HERMES_HOME/time-machine/ with durable
manifests (timestamp, label, trigger/source, profile, files present,
content hashes, size), dedup (identical content stored once), atomic
publish (partial + os.replace), and cross-process locking (fcntl/msvcrt)
for multi-profile concurrency. Implementation milestone.
"""
