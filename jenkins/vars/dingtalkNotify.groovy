/**
 * dingtalkNotify.groovy — Jenkins 共享库函数
 *
 * 在 Pipeline 中检测到特殊文件变更时，通过转发器向钉钉发起审批，
 * 等待 Leader 审批通过后继续执行。
 *
 * 用法:
 *   @Library('jenkins-shared-lib') _
 *   def approved = dingtalkNotify(
 *       jobName: env.JOB_NAME,
 *       buildId: env.BUILD_ID,
 *       changedFiles: 'config/production.yaml',
 *       approvers: 'manager001,manager002'
 *   )
 *   if (!approved) { error("审批未通过") }
 */

def call(Map args) {
    def jobName = args.jobName ?: env.JOB_NAME
    def buildId = args.buildId ?: env.BUILD_ID
    def changedFiles = args.changedFiles ?: 'unknown'
    def approvers = args.approvers ?: ''
    def title = args.title ?: "Jenkins 构建审批: ${jobName}"
    def timeout = args.timeout ?: 3600
    def relayUrl = args.relayUrl ?: env.JD_RELAY_URL

    if (!approvers) {
        error("dingtalkNotify: approvers 参数不能为空")
    }

    def content = """
        **Job**: ${jobName}
        **Build**: #${buildId}
        **变更文件**: ${changedFiles}
        **发起人**: ${env.BUILD_USER_ID ?: 'jenkins'}
        **时间**: ${new Date().format('yyyy-MM-dd HH:mm:ss')}
    """.stripIndent().trim()

    echo "============================================"
    echo "  向钉钉发起审批: ${title}"
    echo "  审批人: ${approvers}"
    echo "============================================"

    // 调用 CLI 发起审批
    def approvalOutput = sh(
        script: """
            jdcli request-approval \
                --job "${jobName}" \
                --build ${buildId} \
                --title "${title}" \
                --content "${content}" \
                --approvers "${approvers}"
        """,
        returnStdout: true
    ).trim()

    echo "审批请求结果: ${approvalOutput}"

    // 从输出中提取 approval_id（最后一行）
    def lines = approvalOutput.readLines()
    def approvalId = lines.last().trim()

    if (!approvalId || approvalId.contains("错误")) {
        error("发起审批失败: ${approvalOutput}")
    }

    echo "审批 ID: ${approvalId}"

    // 轮询等待审批结果
    echo "等待审批结果 (超时: ${timeout}s)..."

    def waitResult = sh(
        script: """
            jdcli wait-approval \
                --id "${approvalId}" \
                --timeout ${timeout} \
                --poll 5
        """,
        returnStdout: true,
        returnStatus: true
    )

    def exitCode = waitResult[0]
    def output = waitResult[1]

    echo "审批结果: ${output}"

    if (exitCode != 0 || !output.contains("APPROVED")) {
        error("审批未通过或超时: ${output}")
    }

    echo "============================================"
    echo "  审批通过! 继续执行构建"
    echo "============================================"

    return true
}
