/**
 * Why a reading did not happen, from the status the API answered with.
 *
 * Its own file because two callers now classify the same failures -- the
 * speaker button on a message, and voice mode -- and a model that serves one
 * reader at a time makes "busy" a routine outcome rather than an edge case. Two
 * copies of this mapping would drift, and the one that drifted would report a
 * queued reading as a broken one.
 */

export type SpeechError = "busy" | "unavailable" | "failed";

export function speechErrorKind(status?: number): SpeechError {
  // 503 is the model already reading something else; 422 is the passage being
  // refused outright. Everything else is a failure the user can only retry.
  if (status === 503) return "busy";
  if (status === 422) return "unavailable";
  return "failed";
}
