# creator_01 fixed voice reference

The approved Seed-VC target voice is stored outside GitHub as a binary FLAC file in Google Drive:

`MeetMap TikTok Content / Voice References / creator_01_reference.flac`

Runtime identity:

- Drive file ID: `1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt`
- SHA-256: `e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7`
- Local ComfyUI path: `input/meetmap_refs/creator_01/voice/reference.flac`
- Duration: approximately 10.03 seconds
- Format: mono FLAC, 44.1 kHz, 16-bit PCM

`MeetMapCreatorVoiceReference` downloads the file through the configured Google service account when the local copy is missing and verifies the SHA-256 before returning an `AUDIO` object to Seed-VC.

The legacy workflow value `meetmap_refs/creator_01/voice/reference.wav` is automatically redirected to the FLAC path for backward compatibility.

Do not replace the Drive file in place without also updating the approved SHA-256 in:

- `voice_nodes.py`
- `runpod_scail_v3.env.example`
- `refs/creators/creator_01/profile.json`

Only use a voice recording owned by the project or supplied with clear permission from the speaker.
