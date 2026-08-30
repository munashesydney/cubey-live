# Cubey wake-word assets

`cubey_multigreeting_v2.onnx` is the custom binary classifier trained for the
"cue-bee" pronunciation and greeting variants. The runtime score threshold is
configured with `OPENWAKEWORD_THRESHOLD` and defaults to `0.5`.

V2 adds hard-negative mining for ordinary speech, phone-band speech, keyboard
noise, mouse clicks, and desk impacts. The deployed classifier has 64 hidden
units; its runtime interface remains identical to V1.

The classifier depends on openWakeWord's shared ONNX mel-spectrogram and audio
embedding models, which are included here to make Raspberry Pi deployment
deterministic. The openWakeWord source is Apache-2.0. Upstream clarification of
the separately distributed shared feature-model binaries is still unresolved,
so this build should be treated as an evaluation deployment rather than a
commercially cleared artifact.

SHA-256:

- `cubey_multigreeting_v2.onnx`: `1b383b6a70a1a06ac9a96275fed8c310d32baa2cb180d8c100ca5456ea0ad6e1`
- `embedding_model.onnx`: `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f`
- `melspectrogram.onnx`: `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f`

At threshold `0.5`, standalone synthetic validation measured 50.6% recall,
5.7 general-speech false activations/hour, 0 typing false activations/hour, and
0.9 phone-speech false activations/hour. Field behavior remains the release
criterion.
