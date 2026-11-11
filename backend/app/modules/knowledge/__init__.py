"""Bootstrap the canonical ORM registry before independently loaded workers.

app.models registers these domain models and owns the shared base mixins.
Loading it at the package boundary lets either import order finish normally,
without a second model registry or duplicate ORM definitions. No DB is opened.
"""
import app.models  # noqa: F401
