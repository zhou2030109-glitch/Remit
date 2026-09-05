import styles from "./DesignPanel.module.css";

type Stat = { label: string; value: string; hint?: string };

const defaultStats: Stat[] = [
  { label: "Uptime", value: "99.98%", hint: "30d" },
  { label: "Latency", value: "84ms", hint: "p95" },
  { label: "Migrations", value: "12", hint: "this week" },
];

export function DesignPanel({
  title = "System Brief",
  subtitle = "A compact panel designed for strong hierarchy and quick scanning.",
  stats = defaultStats,
  ctaLabel = "View details",
}: {
  title?: string;
  subtitle?: string;
  stats?: Stat[];
  ctaLabel?: string;
}) {
  return (
    <section className={styles.shell} aria-label={title}>
      <div className={styles.head}>
        <div className={styles.kicker}>Field Report</div>
        <h2 className={styles.title}>{title}</h2>
        <p className={styles.subtitle}>{subtitle}</p>
      </div>

      <div className={styles.grid} role="list">
        {stats.map((s) => (
          <article key={s.label} className={styles.card} role="listitem">
            <div className={styles.cardTop}>
              <div className={styles.cardLabel}>{s.label}</div>
              {s.hint ? <div className={styles.cardHint}>{s.hint}</div> : null}
            </div>
            <div className={styles.cardValue}>{s.value}</div>
          </article>
        ))}
      </div>

      <div className={styles.foot}>
        <button className={styles.cta} type="button">
          <span className={styles.ctaText}>{ctaLabel}</span>
          <span className={styles.ctaIcon} aria-hidden="true">
            →
          </span>
        </button>
      </div>
    </section>
  );
}
