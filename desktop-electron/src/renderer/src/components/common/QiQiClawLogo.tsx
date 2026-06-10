function QiQiClawLogo({ size = 32 }: { size?: number }): React.JSX.Element {
  return (
    <span
      className="qiqiclaw-logo-letter"
      style={{ width: size, height: size, fontSize: Math.max(14, size * 0.56) }}
      aria-label="QiQiClaw"
      role="img"
    >
      Q
    </span>
  );
}

export default QiQiClawLogo;
