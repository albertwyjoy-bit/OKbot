DMail allows you to send a message to the past.

You can see some `user` messages with `CHECKPOINT {checkpoint_id}` wrapped in `<system>` tags in the context. When you need to send a DMail, select one of the checkpoint IDs in these messages as the destination checkpoint ID.

When a DMail is sent, the system will revert the current context to the specified checkpoint. After reverting, you will no longer see any messages which you can currently see after that checkpoint. The message in the DMail will be appended to the end of the context. So, next time you will see all the messages before the checkpoint, plus the message in the DMail. You must make it very clear in the DMail message, tell your past self what you have done/changed, what you have learned and any other information that may be useful.

When sending a DMail, DO NOT do much explanation to the user. The user do not care about this. Just explain to your past self.

Here are some typical scenarios you may want to send a DMail:

- You read a file, found it very large and most of the content is not relevant to the current task. In this case you can send a DMail to the checkpoint before you read the file and give your past self only the useful part.
- You searched the web, found the result very large.
  - If you got what you need, you may send a DMail to the checkpoint before you searched the web and give your past self the useful part.
  - If you did not get what you need, you may send a DMail to tell your past self to try another query.
- You wrote some code, found it not working or had some compilation/lint errors. You spent many struggling steps to fix it but the process is not relevant to the ultimate goal. In this case you can send a DMail to the checkpoint before you wrote the code and give your past self the fixed version of the code and tell yourself no need to write it again because you already wrote to the filesystem.
