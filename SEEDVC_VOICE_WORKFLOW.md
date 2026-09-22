# MeetMap Seed-VC voice workflow

The Drive character-swap workflow now converts the source speaker to one fixed `creator_01` voice.

## Audio path

```text
Queue source video
  -> GetVideoComponents
  -> TrimAudioDuration to generated clip length
  -> MeetMapCreatorVoiceReference
       -> local verified FLAC, or
       -> automatic Google Drive download when missing
  -> SeedVCRun
  -> final CreateVideo
  -> SaveVideo
  -> move source to Already posted
```

## Fixed creator voice

Google Drive:

`MeetMap TikTok Content / Voice References / creator_01_reference.flac`

- Drive file ID: `1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt`
- SHA-256: `e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7`
- Local RunPod path: `ComfyUI/input/meetmap_refs/creator_01/voice/reference.flac`

The same file and hash are used for every render. This prevents accidental voice drift caused by changing reference recordings.

## Seed-VC settings

The workflow currently uses:

- steps: `30`
- speed: `1.0`
- inference CFG: `0.7`
- F0 conditioning: `false` for speech
- automatic F0 adjustment: `true`
- pitch shift: `0`
- unload model after conversion: `true`

## Failure behavior

The Drive source must not be finalized when any of the following fails:

- creator voice download
- SHA-256 verification
- audio decoding
- Seed-VC conversion
- final audio/video mux
- SaveVideo

The `Already posted` move remains downstream of `SaveVideo`, so failed voice conversion leaves the original source in `Queue` for retry.

## Current limitation

This first version sends the complete trimmed source audio through Seed-VC. Strong music or environmental audio can therefore be affected. A later version should separate vocals and accompaniment before conversion, process only the vocal stem, and mix the untouched background stem back afterward.

Use only voice references that the project owns or has explicit permission to use.
