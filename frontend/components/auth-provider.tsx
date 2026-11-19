"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import type { Session, SupabaseClient, User } from "@supabase/supabase-js";

import { createClient } from "@/lib/supabase/client";

type AuthResult = {
  error: string | null;
  requiresEmailConfirmation?: boolean;
};

type SupabaseAuthContextValue = {
  client: SupabaseClient | null;
  configured: boolean;
  loading: boolean;
  session: Session | null;
  user: User | null;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signUp: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<AuthResult>;
};

const SupabaseAuthContext = createContext<SupabaseAuthContextValue | null>(null);

export function SupabaseAuthProvider({ children }: { children: ReactNode }) {
  const [client] = useState<SupabaseClient | null>(() => createClient());
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!client) {
      setLoading(false);
      return;
    }

    let mounted = true;
    void client.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      setSession(data.session);
      setLoading(false);
    });

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((_event, nextSession) => {
      if (!mounted) return;
      setSession(nextSession);
      setLoading(false);
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, [client]);

  async function signIn(email: string, password: string): Promise<AuthResult> {
    if (!client) return { error: "Supabase belum dikonfigurasi." };
    const { error } = await client.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }

  async function signUp(email: string, password: string): Promise<AuthResult> {
    if (!client) return { error: "Supabase belum dikonfigurasi." };
    const { data, error } = await client.auth.signUp({ email, password });
    return {
      error: error?.message ?? null,
      requiresEmailConfirmation: !error && !data.session,
    };
  }

  async function signOut(): Promise<AuthResult> {
    if (!client) return { error: "Supabase belum dikonfigurasi." };
    const { error } = await client.auth.signOut();
    return { error: error?.message ?? null };
  }

  return (
    <SupabaseAuthContext.Provider
      value={{
        client,
        configured: client !== null,
        loading,
        session,
        user: session?.user ?? null,
        signIn,
        signUp,
        signOut,
      }}
    >
      {children}
    </SupabaseAuthContext.Provider>
  );
}

export function useSupabaseAuth() {
  const context = useContext(SupabaseAuthContext);
  if (!context) {
    throw new Error("useSupabaseAuth must be used within SupabaseAuthProvider");
  }
  return context;
}
