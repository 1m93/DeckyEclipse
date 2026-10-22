type Obj = Record<string, unknown>;

function isPlainObject(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Recursive merge matching the backend semantics: `null` deletes a key,
 * arrays and primitives replace, objects merge.
 */
export function deepMerge<T>(base: T, patch: unknown): T {
  if (!isPlainObject(base) || !isPlainObject(patch)) return patch as T;
  const out: Obj = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) {
      delete out[key];
    } else if (isPlainObject(value) && isPlainObject(out[key])) {
      out[key] = deepMerge(out[key], value);
    } else {
      out[key] = value;
    }
  }
  return out as T;
}

/** Combine two patches; unlike {@link deepMerge}, `null` deletions are preserved. */
export function mergePatches<T>(first: T, second: unknown): T {
  if (!isPlainObject(first) || !isPlainObject(second)) return second as T;
  const out: Obj = { ...first };
  for (const [key, value] of Object.entries(second)) {
    out[key] = isPlainObject(value) && isPlainObject(out[key]) ? mergePatches(out[key], value) : value;
  }
  return out as T;
}
