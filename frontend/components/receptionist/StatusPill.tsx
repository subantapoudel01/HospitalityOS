export function StatusPill({ status }: { status: string }) {
  return <span className={"pill pill-" + status}>{status}</span>;
}
