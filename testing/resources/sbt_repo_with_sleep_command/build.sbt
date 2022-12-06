import SleepCommand._

lazy val root = (project in file("."))
  .settings(
    commands ++= Seq(sleepCommand)
  )
