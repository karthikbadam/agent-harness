import { api } from "./client";
import type { PushSubscribeIn, PushSubscriptionOut, VapidKey } from "../types";

export const pushApi = {
  vapidKey: () => api.get<VapidKey>("/api/push/vapid-public-key"),
  subscribe: (body: PushSubscribeIn) =>
    api.post<PushSubscriptionOut>("/api/push/subscribe", body),
  unsubscribe: (id: string) => api.del(`/api/push/subscribe/${id}`),
  list: () => api.get<PushSubscriptionOut[]>("/api/push/subscriptions"),
};
