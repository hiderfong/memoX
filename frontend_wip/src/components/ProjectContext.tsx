import { createContext, useCallback, useContext, useEffect, useMemo, useState, type FC, type ReactNode } from 'react';

import { api } from '../shared';

export const PROJECT_KEY = 'memox_current_project_id';

export interface ProjectSummary {
  id: string;
  name: string;
  color: string;
  health?: string;
  metrics?: Record<string, any>;
}

interface ProjectContextValue {
  projects: ProjectSummary[];
  selectedProjectId: string;
  selectedProject?: ProjectSummary;
  projectGroupIds: string[] | null;
  loading: boolean;
  setSelectedProjectId: (id: string) => void;
  refreshProjects: () => Promise<void>;
}

const ProjectContext = createContext<ProjectContextValue>({
  projects: [],
  selectedProjectId: '',
  selectedProject: undefined,
  projectGroupIds: null,
  loading: false,
  setSelectedProjectId: () => {},
  refreshProjects: async () => {},
});

export const ProjectProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedProjectId, setSelectedProjectIdState] = useState(() => {
    try {
      return localStorage.getItem(PROJECT_KEY) || '';
    } catch {
      return '';
    }
  });

  const setSelectedProjectId = useCallback((id: string) => {
    const nextId = id || '';
    setSelectedProjectIdState(nextId);
    try {
      if (nextId) localStorage.setItem(PROJECT_KEY, nextId);
      else localStorage.removeItem(PROJECT_KEY);
    } catch {}
  }, []);

  const refreshProjects = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listProjects();
      const nextProjects = Array.isArray(res.data?.projects) ? res.data.projects : [];
      setProjects(nextProjects);
      setSelectedProjectIdState((current) => {
        if (!current || nextProjects.some((project: ProjectSummary) => project.id === current)) {
          return current;
        }
        try { localStorage.removeItem(PROJECT_KEY); } catch {}
        return '';
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshProjects().catch(() => {});
  }, [refreshProjects]);

  const selectedProject = useMemo(
    () => projects.find(project => project.id === selectedProjectId),
    [projects, selectedProjectId],
  );
  const projectGroupIds = useMemo(() => (
    selectedProjectId ? [selectedProjectId] : null
  ), [selectedProjectId]);

  const value = useMemo<ProjectContextValue>(() => ({
    projects,
    selectedProjectId,
    selectedProject,
    projectGroupIds,
    loading,
    setSelectedProjectId,
    refreshProjects,
  }), [projects, selectedProjectId, selectedProject, projectGroupIds, loading, setSelectedProjectId, refreshProjects]);

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
};

export const useProjectContext = () => useContext(ProjectContext);
