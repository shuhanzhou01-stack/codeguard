# Third-party notices

CodeGuard's own source is licensed under [Apache-2.0](LICENSE). Dependencies and base images are **not relicensed** by that file; they retain their respective upstream terms. No third-party source is intentionally vendored into this repository.

The pinned Python dependencies include MIT, BSD-3-Clause, and Apache-2.0 packages. Notably, `psycopg` and `psycopg-binary` declare **LGPL-3.0-only**. They are used as separate dependencies, not copied into CodeGuard source. Anyone distributing a built image or dependency bundle should retain upstream copyright/license notices and check the obligations of Psycopg and any bundled native components. The pinned versions and exact dependency set are in `requirements.txt` and `requirements-dev.txt`; this document is not an exhaustive SBOM.

Docker base images (`python:3.13-slim`, `postgres:17-alpine`, and `redis:7-alpine`) also carry independent package licenses. Review their upstream notices when redistributing an image, rather than treating this repository's Apache-2.0 license as covering the entire image.

The design discusses other public developer tools and evaluation projects for context; those names do not imply endorsement or transfer of their licenses.
