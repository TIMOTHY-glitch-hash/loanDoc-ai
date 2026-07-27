'use client';

import { ConvexProvider, ConvexReactClient } from 'convex/react';
import { useState, type ReactNode } from 'react';
import { env } from '@/lib/env';

/**
 * Wraps the tree in a Convex client.
 *
 * The client is created inside `useState` rather than at module scope so it is
 * built once per browser session and never during a server render (a module-level
 * client would be instantiated on the server too, where the websocket is useless).
 */
export function ConvexClientProvider({ children }: { children: ReactNode }) {
  const [client] = useState(() => new ConvexReactClient(env.NEXT_PUBLIC_CONVEX_URL));

  return <ConvexProvider client={client}>{children}</ConvexProvider>;
}
