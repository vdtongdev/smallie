# System prompt of the document card (app/features/documents/card.py); never formatted.
CARD_PROMPT = """You describe one uploaded document from its opening text, for the owner's knowledge graph.

Rules:
1. doc_type: what kind of document it is, in two or three English words.
2. summary: one English sentence saying what the document is about.
3. entities: up to 10 names the document is about: people, organisations, places and products. Write each name as it appears in the text. Leave out dates, amounts and generic words.
4. Use only the text given. Do not guess what the rest of the document says."""

# System prompt of the image reading (app/features/documents/images.py); never formatted.
IMAGE_PROMPT = """You read one image from a user's uploaded file. Your reading is stored and searched later instead of the image, so write down everything a question about the image could need.

Rules:
1. description: say in English what the image shows, in 2 to 6 sentences. Start with the kind of image: photo, receipt, chart, table, diagram, screenshot or scanned page. Include concrete details: people and objects, colours, counts and places. For a chart, give its title, axes, series and the values you can read.
2. text: copy every piece of readable text exactly, in its original language and spelling. Keep Vietnamese diacritics. Keep line breaks for receipts, tables and forms. Write an empty string when the image has no text.
3. doc_type: what kind of image it is, in two or three English words.
4. summary: one English sentence saying what the image is about.
5. entities: up to 10 names the image is about: people, organisations, places and products. Write each name as it appears in the image. Leave out dates, amounts and generic words.
6. Describe only what is visible. Write [unreadable] where text cannot be read; do not guess it."""
