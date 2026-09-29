import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(_: NextRequest) {
  // Allow request to proceed to client-side shell, which performs tab-isolated sessionStorage validation
  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all dashboard routes except for:
     * - api routes (handled directly or by FastAPI proxy)
     * - _next/static (static files)
     * - _next/image (image optimization)
     * - favicon.ico, fonts, and images
     */
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|woff|woff2)$).*)",
  ],
};
