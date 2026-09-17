/** Hash router: '#/photos', '#/search?q=…', '#/albums/3', '#/people/2', … */

export type Route =
  | { page: 'photos' }
  | { page: 'explore' }
  | { page: 'search'; query: string }
  | { page: 'albums'; albumId?: number }
  | { page: 'people'; personId?: number }
  | { page: 'places' }
  | { page: 'trash' }
  | { page: 'archive' }
  | { page: 'locked' }
  | { page: 'utilities' }
  | { page: 'settings' };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [pathPart, queryPart] = raw.split('?');
  const seg = pathPart.split('/').filter(Boolean);
  const params = new URLSearchParams(queryPart || '');
  switch (seg[0] || 'photos') {
    case 'search': return { page: 'search', query: params.get('q') || '' };
    case 'albums': return { page: 'albums', albumId: seg[1] ? Number(seg[1]) : undefined };
    case 'people': return { page: 'people', personId: seg[1] ? Number(seg[1]) : undefined };
    case 'explore': return { page: 'explore' };
    case 'places': return { page: 'places' };
    case 'trash': return { page: 'trash' };
    case 'archive': return { page: 'archive' };
    case 'locked': return { page: 'locked' };
    case 'utilities': return { page: 'utilities' };
    case 'settings': return { page: 'settings' };
    default: return { page: 'photos' };
  }
}

export function hrefFor(route: Route): string {
  switch (route.page) {
    case 'photos': return '#/photos';
    case 'explore': return '#/explore';
    case 'search': return `#/search?q=${encodeURIComponent(route.query)}`;
    case 'albums': return route.albumId != null ? `#/albums/${route.albumId}` : '#/albums';
    case 'people': return route.personId != null ? `#/people/${route.personId}` : '#/people';
    default: return `#/${route.page}`;
  }
}
