# Cubey wake-word assets

`cubey_multigreeting_v1.onnx` is the custom binary classifier trained for the
"cue-bee" pronunciation and greeting variants. The runtime score threshold is
configured with `OPENWAKEWORD_THRESHOLD` and defaults to `0.5`.

The classifier depends on openWakeWord's shared ONNX mel-spectrogram and audio
embedding models, which are included here to make Raspberry Pi deployment
deterministic. The openWakeWord source is Apache-2.0. Upstream clarification of
the separately distributed shared feature-model binaries is still unresolved,
so this build should be treated as an evaluation deployment rather than a
commercially cleared artifact.

SHA-256:

- `cubey_multigreeting_v1.onnx`: `ed4d6330fd821d097433d226dfdfb73aa38c2295bb40605017c9e851bd3996d0`
- `embedding_model.onnx`: `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f`
- `melspectrogram.onnx`: `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f`

Standalone synthetic validation for this first candidate measured 42.9% recall
at threshold `0.5`; field testing and retraining are expected before release.
