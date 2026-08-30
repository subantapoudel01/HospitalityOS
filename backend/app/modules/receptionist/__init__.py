"""
AI Receptionist module.

Owns guest-conversation concerns end to end. May import from app.platform
(hotels, rooms, rates) and app.core (config, db, model_router); must not be
imported by app.platform.

Tables owned here (see docs/01-platform/DATABASE_DESIGN.md):
    conversations, messages, booking_inquiries, knowledge_documents,
    knowledge_chunks, hotel_policies, faqs, ai_requests
"""
