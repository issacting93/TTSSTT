# TTSSTT

A Text-to-Speech (TTS) and Speech-to-Text (STT) application pipeline.

## Features
- PDF parsing and text extraction.
- Text-to-Speech synthesis (supporting various models/APIs).
- Speech-to-Text transcription.
- Designed with scalability for long-form content in mind.

## Project Structure
- `app.py`: Main application logic.
- `plan.md`: Research and architectural planning for scaling the pipeline.
- `requirements.txt`: Python dependencies.
- `static/`: Frontend assets (JS, CSS).
- `templates/`: HTML templates.

## Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python app.py
   ```

## Future Roadmap
- Streaming synthesis for long documents.
- Progressive playback.
- Resumable jobs and smart chunking.
- Self-hosting options for cost-effective synthesis.
