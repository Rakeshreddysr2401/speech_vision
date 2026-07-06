Goal:To have a complete working prototype the only difference is instead of having Real Robot we need to have proper simulator in this laptop where we can change environment,robot all as we need later will replace with real robot.

Items:
Mac_Mini: To Run Local VLM using Lamma (capable of giving coordinates for objects to go)
Pi5: It's uses Langgraph with Bridged ROS + Micro ROS with Real Rover(but instead we need to connect to Simulator Which is Running in Some Other Laptop)
Isaac: two containers
       ->isaac_ros-dev: it is provided by nvidia (need to have nvblox,slam,nav2 all what ever we required uses NITROS works good , also need to have realsense rgbd taking and kind of suff) - currently not have all this will install but implement the code to align with this.
       ->ai_stack (if possible and have efficiency to run ok fine use it other wise will chat directly from pi5 but try to have these too)
Windows Laptop: Here we having ROS2 with simulated code where we can design robot,change environment completly all related to simulation (Like Robot with Depth Camera, Lidar). Later will add real ROBO(Make Sure To Have Clear Code with All The Things mentioned clearly)


Things I Need/Acheive with This:
1.I need flexibility to Change Environemnts and Robo Architure (HelpFull In Testing)
2.If I ask Hey Go Near TV Tabel It Needs to move from To TV Table Not Point weather it is in Hall or Bedroom
3.If I say Save This Location As Hall or TV Area or Caffiteria It Needs To Save And Helps In Directing Next Time.
4.If Ask Go Near The Cup It Needs to Find Cup Infront of it if not found need to search by Rotating Its Camera Left and Right And Moving Autonomously
Once It Finds Need to Get Its Coordinates And Needs to Move Near To It.
5.If I ask Go Near To Table take a pic and send it Teligram and come back it needs to have that proper planing and do things (Use Langgraph and its stuff to acheive It).


As of Now Fast Ness Not That Matters Fine Even if it takes time.

Important: Make Sure To Have Required Things in Proper Places And Flexibility To Add Real Robot Easily In Future.

Plan Accordingly First And Proceed If any doubts get clarify 

This Current repo in Laptop Is Trail Which I made previosly please have super cool structure and code of prod level how community follows



