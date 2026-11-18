export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="notice" aria-busy>{label}</div>;
}

export function QueryError({ error }: { error: { message: string } }) {
  return <div className="notice error">Couldn&apos;t load this view: {error.message}</div>;
}
