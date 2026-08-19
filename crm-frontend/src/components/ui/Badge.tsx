type Tone = "green" | "amber" | "gray" | "blue" | "red" | "teal";
const TONES: Record<Tone, string> = {
  green: "bg-status-greenbg text-status-green",
  amber: "bg-status-amberbg text-status-amber",
  gray: "bg-status-graybg text-status-gray",
  blue: "bg-status-bluebg text-status-blue",
  red: "bg-status-redbg text-status-red",
  teal: "bg-teal-soft text-teal-700",
};
export function Badge({ tone, children, dot }: { tone: Tone; children: React.ReactNode; dot?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${TONES[tone]}`}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
