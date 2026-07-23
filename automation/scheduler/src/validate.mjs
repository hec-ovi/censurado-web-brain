// Fail-closed validation against the layer's own JSON Schema files, with no
// dependency. Supports exactly the subset those schemas use: type, properties,
// required, additionalProperties:false, items, minItems, enum, pattern,
// minLength, minimum, and local "$ref": "#/$defs/<name>". Anything else in a
// schema is a bug in the schema, not a silently-ignored feature: unknown
// keywords throw, so the validator can never quietly under-validate.

const KNOWN = new Set([
  '$schema', '$id', '$defs', '$ref', 'title', 'type', 'properties', 'required',
  'additionalProperties', 'items', 'minItems', 'enum', 'pattern', 'minLength',
  'minimum',
])

function resolveRef(ref, root) {
  const m = /^#\/\$defs\/([A-Za-z0-9_-]+)$/.exec(ref)
  if (m === null || root.$defs?.[m[1]] === undefined) {
    throw new Error(`unresolvable $ref ${ref}`)
  }
  return root.$defs[m[1]]
}

function typeOf(value) {
  if (Array.isArray(value)) return 'array'
  if (value === null) return 'null'
  if (typeof value === 'number') return Number.isInteger(value) ? 'integer' : 'number'
  return typeof value
}

function check(value, schema, root, path, out) {
  for (const key of Object.keys(schema)) {
    if (!KNOWN.has(key)) throw new Error(`unsupported schema keyword ${key} at ${path}`)
  }
  if (schema.$ref !== undefined) {
    check(value, resolveRef(schema.$ref, root), root, path, out)
    return
  }
  const actual = typeOf(value)
  if (schema.type !== undefined) {
    const ok = schema.type === actual || (schema.type === 'number' && actual === 'integer')
    if (!ok) {
      out.push(`${path}: expected ${schema.type}, got ${actual}`)
      return
    }
  }
  if (schema.enum !== undefined && !schema.enum.includes(value)) {
    out.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`)
  }
  if (actual === 'string') {
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      out.push(`${path}: ${JSON.stringify(value)} does not match ${schema.pattern}`)
    }
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      out.push(`${path}: shorter than minLength ${schema.minLength}`)
    }
  }
  if ((actual === 'integer' || actual === 'number')
      && schema.minimum !== undefined && value < schema.minimum) {
    out.push(`${path}: ${value} is below minimum ${schema.minimum}`)
  }
  if (actual === 'array') {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      out.push(`${path}: fewer than minItems ${schema.minItems}`)
    }
    if (schema.items !== undefined) {
      value.forEach((item, i) => check(item, schema.items, root, `${path}[${i}]`, out))
    }
  }
  if (actual === 'object') {
    for (const name of schema.required ?? []) {
      if (value[name] === undefined) out.push(`${path}: missing required ${name}`)
    }
    for (const [name, sub] of Object.entries(schema.properties ?? {})) {
      if (value[name] !== undefined) check(value[name], sub, root, `${path}.${name}`, out)
    }
    if (schema.additionalProperties === false) {
      const known = new Set(Object.keys(schema.properties ?? {}))
      for (const name of Object.keys(value)) {
        if (!known.has(name)) out.push(`${path}: unknown property ${name}`)
      }
    }
  }
}

// Returns the list of violations; empty means the value conforms.
export function validate(value, schema) {
  const out = []
  check(value, schema, schema, '$', out)
  return out
}
