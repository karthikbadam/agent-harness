import { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Center, Spinner } from "@chakra-ui/react";

import { useAuth } from "./hooks/useAuth";
import { AuthGate } from "./pages/AuthGate";
import { JobsPage } from "./pages/Jobs";
import { JobDetailPage } from "./pages/JobDetail";
import { SchedulesPage } from "./pages/Schedules";
import { SettingsPage } from "./pages/Settings";

function RequireAuth({ children }: { children: ReactElement }) {
  const { isAuthed, isLoading, token } = useAuth();
  if (!token) return <Navigate to="/auth" replace />;
  if (isLoading) {
    return (
      <Center minH="100dvh">
        <Spinner />
      </Center>
    );
  }
  if (!isAuthed) return <Navigate to="/auth" replace />;
  return children;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/jobs" replace />} />
      <Route path="/auth" element={<AuthGate />} />
      <Route
        path="/jobs"
        element={
          <RequireAuth>
            <JobsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/jobs/:jobId"
        element={
          <RequireAuth>
            <JobDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/schedules"
        element={
          <RequireAuth>
            <SchedulesPage />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <SettingsPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/jobs" replace />} />
    </Routes>
  );
}
