type AppBackgroundProps = {
  isDark: boolean;
};

export function AppBackground({ isDark }: AppBackgroundProps) {
  return (
    <>
      <div className="pointer-events-none absolute inset-0 overflow-hidden hidden md:block">
        <div
          className={`absolute -top-32 -left-32 h-96 w-96 rounded-full liquid-blob ${
            isDark
              ? "bg-gradient-to-br from-cyan-500/10 via-cyan-600/5 to-transparent"
              : "bg-gradient-to-br from-cyan-400/15 via-cyan-500/5 to-transparent"
          }`}
          style={{ opacity: 0.75 }}
        />
        <div
          className={`absolute top-1/4 -right-24 h-80 w-80 rounded-full liquid-blob ${
            isDark
              ? "bg-gradient-to-bl from-purple-500/10 via-indigo-600/5 to-transparent"
              : "bg-gradient-to-bl from-purple-400/10 via-indigo-500/5 to-transparent"
          }`}
          style={{ animationDelay: "-3s", opacity: 0.65 }}
        />
        <div
          className={`absolute -bottom-20 left-1/3 h-72 w-72 rounded-full liquid-blob ${
            isDark
              ? "bg-gradient-to-tr from-blue-500/10 via-sky-600/5 to-transparent"
              : "bg-gradient-to-tr from-blue-400/10 via-sky-500/5 to-transparent"
          }`}
          style={{ animationDelay: "-5s", opacity: 0.6 }}
        />
      </div>

      <div
        className="pointer-events-none absolute inset-0 hidden opacity-[0.015] md:block"
        style={{
          backgroundImage:
            'url("data:image/svg+xml,%3Csvg viewBox=\'0 0 256 256\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'noise\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23noise)\'/%3E%3C/svg%3E")',
        }}
      />
    </>
  );
}
