import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

async function refreshGoogleToken(refreshToken: string) {
  try {
    const response = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_CLIENT_ID ?? "",
        client_secret: process.env.GOOGLE_CLIENT_SECRET ?? "",
        grant_type: "refresh_token",
        refresh_token: refreshToken,
      }),
      cache: "no-store",
    });
    if (!response.ok) return null;
    const data = await response.json();
    if (!data.access_token) return null;
    return {
      accessToken: data.access_token as string,
      expiresAt: Math.floor(Date.now() / 1000) + (data.expires_in ?? 3540),
      refreshToken: (data.refresh_token as string | undefined) ?? refreshToken,
    };
  } catch {
    return null;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  session: { strategy: "jwt" },
  pages: { error: "/" },
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
      authorization: {
        params: {
          scope:
            "openid email profile https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive",
          access_type: "offline",
          prompt: "consent",
        },
      },
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        token.accessToken = account.access_token ?? undefined;
        token.refreshToken = account.refresh_token ?? undefined;
        token.expiresAt = account.expires_at ?? undefined;
      }
      const expiresAt = token.expiresAt;
      const needsRefresh =
        typeof expiresAt === "number" && Date.now() > expiresAt * 1000 - 60_000;
      if (needsRefresh && token.refreshToken) {
        const refreshed = await refreshGoogleToken(token.refreshToken);
        if (refreshed) {
          token.accessToken = refreshed.accessToken;
          token.expiresAt = refreshed.expiresAt;
          token.refreshToken = refreshed.refreshToken;
        }
      }
      return token;
    },
    async session({ session, token }) {
      session.accessToken = typeof token.accessToken === "string" ? token.accessToken : undefined;
      return session;
    },
  },
});
