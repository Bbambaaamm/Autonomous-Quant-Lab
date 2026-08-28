"use client";

import { useActionState } from "react";
import type { ActionState } from "@/app/actions";

export function MutationForm({ action, title, submit, children, disabled=false, danger=false }: { action:(state:ActionState,form:FormData)=>Promise<ActionState>; title:string; submit:string; children:React.ReactNode; disabled?:boolean; danger?:boolean }) {
  const [state, formAction, pending] = useActionState(action, {});
  return <form action={formAction} className="card mutation-form"><h3>{title}</h3>{children}<p className="paper-note">PAPER ONLY · změna se provede až po autoritativní odpovědi serveru.</p>{state.error&&<p role="alert" className="error">{state.error}</p>}{state.success&&<p role="status" className="success">{state.success}</p>}<button className={danger?"danger":undefined} disabled={disabled||pending} aria-disabled={disabled||pending}>{pending?"Ověřuji na serveru…":submit}</button></form>;
}
