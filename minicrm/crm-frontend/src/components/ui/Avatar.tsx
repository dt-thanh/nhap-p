export function Avatar({ src, name, size = 36 }: { src?: string; name: string; size?: number }) {
  const initials = name.split(" ").slice(-2).map((w) => w[0]).join("").toUpperCase();
  if (src) {
    return <img src={src} alt={name} width={size} height={size} className="shrink-0 rounded-full object-cover" style={{ width: size, height: size }} />;
  }
  return (
    <div className="flex shrink-0 items-center justify-center rounded-full bg-navy-600 font-medium text-white" style={{ width: size, height: size, fontSize: size * 0.36 }}>
      {initials}
    </div>
  );
}
