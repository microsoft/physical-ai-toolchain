export function redactOperatorText(value: string): string {
  return value
    .replace(/(?:\/dev\/|\/home\/|\/Users\/|[A-Za-z]:\\)\S+/g, '[redacted path]')
    .replace(/\b(serial(?: number| id)?)[\s:=]+[\w.-]+/gi, '$1 [redacted]')
}
