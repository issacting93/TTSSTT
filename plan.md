Short answer: not without changes. Long documents break this pipeline at three distinct places, each with its own fix.

**Where it breaks**

*Ingest.* GROBID and Marker handle 100+ page PDFs fine, but memory spikes on books. Browser-based pdf.js will choke. Move PDF parsing to the worker, never the browser.

*TTS.* Per-request character limits are the real wall. OpenAI TTS caps at 4,096 chars (~1 minute of audio). A 300-page book is roughly 600,000 chars → 150+ API calls. Issues that emerge at this scale:
- Voice drift between chunks (subtle, but audible over an hour)
- Cost: that book is ~$9 on OpenAI, ~$90 on ElevenLabs, ~$0 on a self-hosted Kokoro
- Generation time: serial = an hour, parallel = a few minutes but you'll hit rate limits
- Stitching artifacts at chunk boundaries if you split mid-sentence

*STT.* Whisper API caps at 25MB per file (~30 min of audio). A 10-hour audiobook needs splitting on silence, transcribing in parallel, then merging with timestamp offsets. Doable, but a real piece of code.

**Fixes that scale**

- **Streaming synthesis, not batch.** Don't generate the whole book on upload — generate the first 5 minutes immediately, queue the rest, synthesize ahead of the playback cursor. Most users abandon long content; you save 80%+ of compute.
- **Progressive playback.** Player starts as soon as chunk 1 lands. Manifest file lists all chunks; player fetches sequentially. Same pattern HLS uses for video.
- **Resumable jobs.** Persist chunk-level state. If synthesis fails at chunk 47, resume from 47, not 0. This matters more than it sounds — long jobs *will* fail.
- **Smart chunking.** Split on paragraph or section boundaries from GROBID's structured output, not on character count. Prosody stays clean, you can parallelize freely, and you get natural chapter markers for free.
- **Voice consistency.** Pin the same voice ID + same model version across all chunks of a document. For models that support it (Fish Audio, ElevenLabs), pass a reference audio clip with each request to anchor the voice.
- **Self-host for books.** The economics flip past ~50K characters per document. Kokoro on a single GPU can synthesize a full novel for the cost of electricity. Worth the deployment complexity if long-form is the core use case.
- **For STT: silence-based splitting.** Use ffmpeg's silencedetect, split into 20-min chunks at natural pauses, transcribe in parallel, stitch with offset adjustment. Whisper's word timestamps make merging clean.

**Revised phasing for long-form**

If long documents are the primary use case (sounds like they might be), I'd reorder the MVP:

1. Get the chunking + manifest + progressive player working on a 50-page paper *first*. That's the architectural backbone.
2. Then scale up to a 300-page book. Most bugs surface between 50 and 300 pages — caching invalidation, queue backpressure, voice drift.
3. Add STT after, because the long-form STT problem is largely solved by reusing the chunking infrastructure you already built for TTS.

**One real constraint to know about:** at book length, the bottleneck stops being your code and becomes provider rate limits. OpenAI TTS is 50 requests/min for tier 1. A 600K-char book at 4K chars/chunk is 150 chunks → 3 minutes of pure rate-limit waiting even with perfect parallelism. Plan for tiering up, batching smartly, or self-hosting from day one if you want this to feel responsive.

Want me to sketch the manifest format and chunking logic? That's the piece that, if you get it right early, makes everything else fall into place.