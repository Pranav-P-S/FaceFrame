import { useStore } from '../lib/store';

/** Left navigation rail — Photos / Explore / Library (Google-Photos shape). */

function NavItem({ icon, label, href, active }: { icon: string; label: string; href: string; active: boolean }) {
  return (
    <button
      className={`rail-item ${active ? 'rail-item-active' : ''}`}
      onClick={() => {
        window.location.hash = href;
      }}
    >
      <span className="rail-icon" aria-hidden>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

export default function Rail() {
  const route = useStore((s) => s.route);
  return (
    <nav className="rail" aria-label="Main navigation">
      <div className="rail-section">
        <NavItem icon="▦" label="Photos" href="#/photos" active={route.page === 'photos'} />
        <NavItem icon="✦" label="Explore" href="#/explore" active={route.page === 'explore'} />
      </div>
      <div className="rail-section-label">Library</div>
      <div className="rail-section">
        <NavItem icon="⚙" label="Utilities" href="#/utilities" active={route.page === 'utilities'} />
        <NavItem icon="❐" label="Archive" href="#/archive" active={route.page === 'archive'} />
        <NavItem icon="🗑" label="Trash" href="#/trash" active={route.page === 'trash'} />
        <NavItem icon="🔒" label="Locked" href="#/locked" active={route.page === 'locked'} />
      </div>
    </nav>
  );
}
