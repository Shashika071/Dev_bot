import { ForexPage } from './pages/ForexPage';

/** Forex Rise/Fall only — legacy Touch dashboard/train/accuracy UI removed. */
export default function App() {
  return (
    <div className="app-shell fade-in forex-only">
      <main className="main-content forex-main">
        <ForexPage />
      </main>
    </div>
  );
}
