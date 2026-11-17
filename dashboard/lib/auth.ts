declare global {
  interface Window {
    dbvAlerts?: {testSound:()=>Promise<boolean>};
    dbvEditor?: {open:(draftId:string)=>Promise<boolean>};
    dbvAuth?: { getToken: () => Promise<string> };
    dbvMap?: { copyCell: (text: string) => Promise<boolean>; copyPlan: (text: string) => Promise<boolean> };
  }
}

export async function getDashboardToken(): Promise<string> {
  if (!window.dbvAuth) {
    throw new Error("Open the Electron dashboard through start_bot; browser access is disabled.");
  }
  return window.dbvAuth.getToken();
}

export async function authenticatedFetch(url: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await getDashboardToken()}`);
  return fetch(url, { ...init, headers, cache: "no-store" });
}
