"""Full-text search vector on knowledge_chunks (hybrid retrieval)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-01

Vector search alone misses proper nouns. Measured against the Rupakot
corpus before this change:

    "Do you take cash?"          0.397  -> Payment policy      (correct)
    "Can I pay with eSewa?"      0.384  -> Payment policy      (correct)
    "Do you accept eSewa?"       0.189  -> Payment policy      (BELOW FLOOR)
    "Do you take eSewa?"         0.177  -> "How long to allow..." (wrong)
    "Do you take Visa?"          0.240  -> "Begnas Lake and Rupa Lake" (wrong)

The query embedding is dominated by "Do you take" and the brand name
contributes almost nothing, so a lake passage outranks the payment policy.
Lexical matching is exactly the tool for a rare literal token.

WHY A GENERATED COLUMN: Postgres maintains it on every insert and update,
so no application code can forget to. The ALTER backfills existing rows,
which means no re-embedding and no re-ingestion - the 37 existing chunks
become searchable the moment this runs.

WHY THE 'english' CONFIG, verified against this corpus before choosing:
  * It drops the stopwords that were drowning the query. "Do you take
    eSewa?" tokenises to just 'take' and 'esewa'.
  * Brand names survive intact: esewa, visa, fonepay, mastercard, npr.
  * Devanagari and romanized Nepali pass through untouched - the English
    stemmer leaves them alone - so Slice D is unaffected. Checked with
    'चेक-आउट बिहान ११:०० बजे हो' and 'Malai Fonepay bata tirna milcha'.

DEPLOYMENT NOTE: adding a STORED generated column rewrites the table and
takes an ACCESS EXCLUSIVE lock. Instant at pilot scale; on a large corpus
schedule it rather than running it mid-service.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge_chunks
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED
        """
    )
    # GIN, not GiST: GIN is slower to build and faster to search, and this
    # table is written once per ingest and read on every guest message.
    op.execute(
        """
        CREATE INDEX ix_knowledge_chunks_search_vector
        ON knowledge_chunks USING GIN (search_vector)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_search_vector", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "search_vector")
