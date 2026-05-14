import { Button, Flex, Text } from "@chakra-ui/react";

import { useCreateRule } from "../hooks/useAllowlist";
import { useFollowup } from "../hooks/useJobs";

interface Props {
  jobId: string;
  projectId: string;
  rule: string;
  retryPrompt: string;
}

export function AllowlistRetry({ jobId, projectId, rule, retryPrompt }: Props) {
  const create = useCreateRule();
  const followup = useFollowup(jobId);
  const busy = create.isPending || followup.isPending;

  const click = async () => {
    await create.mutateAsync({ rule, project_id: projectId });
    await followup.mutateAsync({ prompt: retryPrompt });
  };

  return (
    <Flex align="center" gap={2} wrap="wrap">
      <Button size="xs" colorPalette="green" onClick={click} loading={busy}>
        Allow <Text as="span" mx={1} fontFamily="mono">{rule}</Text> and retry
      </Button>
    </Flex>
  );
}
