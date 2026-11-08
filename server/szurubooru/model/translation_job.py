import sqlalchemy as sa

from szurubooru.model.base import Base


class TranslationJob(Base):
    """A queued machine-translation request for a post's notes.

    Processed FIFO by the single worker in func/translation_jobs.py.
    """

    __tablename__ = "translation_job"

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    ACTIVE_STATUSES = (STATUS_PENDING, STATUS_RUNNING)

    job_id = sa.Column("id", sa.Integer, primary_key=True)
    post_id = sa.Column(
        "post_id",
        sa.Integer,
        sa.ForeignKey("post.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = sa.Column(
        "user_id",
        sa.Integer,
        sa.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = sa.Column("status", sa.Unicode(16), nullable=False)
    error = sa.Column("error", sa.UnicodeText, nullable=True)
    # How many times the worker has started this job (restart recovery).
    attempts = sa.Column("attempts", sa.Integer, nullable=False, default=0)
    notes_added = sa.Column("notes_added", sa.Integer, nullable=True)
    notes_replaced = sa.Column("notes_replaced", sa.Integer, nullable=True)
    notes_skipped = sa.Column("notes_skipped", sa.Integer, nullable=True)
    # Translator output {width, height, blocks: [{polygon, source,
    # translation, ...}]}; stored for later use, not serialized.
    result = sa.Column("result", sa.JSON, nullable=True)
    creation_time = sa.Column("creation_time", sa.DateTime, nullable=False)
    start_time = sa.Column("start_time", sa.DateTime, nullable=True)
    end_time = sa.Column("end_time", sa.DateTime, nullable=True)

    post = sa.orm.relationship("Post")
    user = sa.orm.relationship("User")
