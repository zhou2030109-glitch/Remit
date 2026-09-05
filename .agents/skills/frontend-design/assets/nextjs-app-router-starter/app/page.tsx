import styles from "./page.module.css";
import { AssistantRail } from "../components/AssistantRail";

const quickWins = [
  {
    title: "Token-first styling",
    body: "Retheme fast by editing CSS variables in globals.css (palette, radii, shadows, motion).",
  },
  {
    title: "One signature move",
    body: "Commit to a single memorable gesture (light sweep, stripe, grain, or brutal border logic).",
  },
  {
    title: "Accessible by default",
    body: "Semantic structure, visible focus, and reduced-motion support are built in.",
  },
];

export default function Page() {
  return (
    <div className={styles.page}>
      <a className={styles.skip} href="#content">
        Skip to content
      </a>

      <header className={styles.topbar}>
        <div className={styles.topbarInner}>
          <a className={styles.brand} href="#">
            <span aria-hidden className={styles.mark} />
            Frontend Design
          </a>
          <nav className={styles.nav} aria-label="Primary">
            <a href="#principles">Principles</a>
            <a href="#cards">Patterns</a>
            <a href="#cta">CTA</a>
          </nav>
        </div>
      </header>

      <main id="content">
        <section className={styles.hero} aria-labelledby="headline">
          <div>
            <h1 id="headline" className={styles.headline}>
              A token-driven page that looks intentional on day one.
            </h1>
            <p className={styles.lede}>
              Build structure first, then motion, then polish. Keep the UI memorable with one
              signature move—not ten random decorations.
            </p>
            <div className={styles.ctaRow} id="cta">
              <a className={`${styles.btn} ${styles.btnPrimary}`} href="#">
                Start a redesign <span aria-hidden>→</span>
              </a>
              <a className={styles.btn} href="#cards">
                See patterns
              </a>
            </div>
          </div>

          <aside className={styles.panel} aria-label="Quick Wins" id="principles">
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>Quick Wins</h2>
              <span className={styles.badge}>Next.js App Router</span>
            </div>
            <ul className={styles.list}>
              {quickWins.map((item) => (
                <li key={item.title} className={styles.item}>
                  <p className={styles.itemTitle}>{item.title}</p>
                  <p className={styles.itemBody}>{item.body}</p>
                </li>
              ))}
            </ul>
          </aside>

          <div className={styles.rail}>
            <AssistantRail />
          </div>
        </section>

        <section className={styles.content} aria-labelledby="cards" id="cards">
          <div className={styles.grid}>
            <article className={styles.card}>
              <h2>Hierarchy</h2>
              <p>Make the primary action obvious, then design the supporting rails.</p>
            </article>
            <article className={styles.card}>
              <h2>Texture</h2>
              <p>Use subtle grain/mesh/stripe logic to avoid flat UI without clutter.</p>
            </article>
            <article className={styles.card}>
              <h2>Motion</h2>
              <p>Orchestrate a few meaningful reveals and micro-interactions—guarded by settings.</p>
            </article>
          </div>
        </section>
      </main>
    </div>
  );
}
