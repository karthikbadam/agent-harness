import { ReactElement, useEffect } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { Center, Spinner } from "@chakra-ui/react";

import { AppToaster } from "./components/AppToaster";
import { useAuth } from "./hooks/useAuth";
import { useJobNotifications } from "./hooks/useJobNotifications";
import { AuthGate } from "./pages/AuthGate";
import { JobsPage } from "./pages/Jobs";
import { JobDetailPage } from "./pages/JobDetail";
import { ProjectsPage } from "./pages/Projects";
import { ProjectDetailPage } from "./pages/ProjectDetail";
import { TaskDetailPage } from "./pages/TaskDetail";
import { SchedulesPage } from "./pages/Schedules";

function RequireAuth({ children }: { children: ReactElement }) {
  const { isAuthed, isLoading, token } = useAuth();
  useJobNotifications();
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

/**
 * Reset window scroll to the top on every forward (PUSH/REPLACE) navigation.
 * Browser back/forward (POP) is left alone so native scroll restoration
 * still returns the user to where they were on the previous page.
 *
 * Without this, scrolling halfway down /projects/:id, clicking a task into
 * /jobs/:jid, then clicking back lands at the same offset on a freshly
 * mounted page — which often shows blank because the new page's content
 * hasn't reached that height yet.
 */
function ScrollOnNav() {
  const { pathname } = useLocation();
  const navType = useNavigationType();
  useEffect(() => {
    if (navType !== "POP") {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
  }, [pathname, navType]);
  return null;
}

export function App() {
  return (
    <>
      <AppToaster />
      <ScrollOnNav />
      <Routes>
        <Route path="/auth" element={<AuthGate />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <ProjectsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/projects/:projectId"
          element={
            <RequireAuth>
              <ProjectDetailPage />
            </RequireAuth>
          }
        />
        <Route
          path="/tasks/:taskId"
          element={
            <RequireAuth>
              <TaskDetailPage />
            </RequireAuth>
          }
        />
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
