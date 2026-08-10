Application – Smart Search
SAAS Solution – S4 HANA Public Cloud
Solution – Developer Extensibility (RAP)
03. HIGH-LEVEL FUNCTIONAL SPECIFICATION
03.01 High-Level Business Requirements
Operation staff use standard app “Manage My Timesheet” to submit timesheet.
The standard search function provides limited search capability. A more robust search function is required.
The key requirements are:
Be able to search work package by sold-to party, project manager, client delivery market, project name, work package name, product group and product.
Be able to add the search result to “Favorite” of “Manage My Timesheet” app.
Smart Search shall support Internal Projects (Scope Item 1A8) in addition to Customer Projects, enabling employees to search and assign Internal Project Work Packages for time recording using “Manage My Timesheet”
 
Smart Search shall support Enterprise Projects (1NT) in addition to Customer Projects and Internal Projects, enabling employees to search and assign Enterprise Project WBS Elements for time recording using "Manage My Timesheet". This enhancement will be delivered as part of wave 2 go-live.
Current State
Project Type
Smart Search Support
Status
Customer Project
✅ Supported
Live
Internal Project (1A8)
✅ In Scope
Planned in Wave 1.1
Enterprise Project
Planned
Planned in Wave 2
 
 
03.02 Related scope items
J12: Time Recording - Project-Based Services
1Q4: Time Recording
J11: Customer Project Management within the Project-Based Services
INT: Project Financial Control <Deferred to future>
 
1A8 - Internal Project Management - Project-Based Services
 
03.03 Overview of Solution
The solution consists of two parts.
Part 1: Search of work package.
A custom search app will be built to provide a robust search of Work Package by sold-to party, project manager, client delivery market, project name, work package name, product group and product.
Part 2: Add search result (work package) to favorite.
Users will be able to select one more multiple work packages from the search result and add them to the relevant project “Team” (resource assignment) section. By doing so, the work package will automatically show up in the “favorite” of “Manage My Timesheet”.
Note: It is technically not feasible to add the search result directly to the “favorite” of “Manage My Timesheet”.
Part3: In case of a job grade change, manage my timesheet app should disallow the time entries when employee choose the work package with the previous job grade and message throws to reassign the work package in smart search
03.04 Key Considerations
03.04.01 Design Principles
The Smart Search app will be a custom app within S/4.
03.04.02 Systems Involved
S/4HANA Public Cloud
03.04.03 Language Requirements
The language will be English.
03.05 Key Design Decisions
03.06 Business Impact if not implemented
If not implemented, users may not be able to find the work package, which will be used to submit timesheet.
Employees working on Internal Projects (1A8) will not be able to easily find and assign Work Packages for time recording, leading to manual workarounds and incorrect postings.
03.07 Workaround
The workaround is to use standard function, but it only allows search by Customer Description and Work Package Name.
04. DETAIL ENHANCEMENT SPECIFICATION
04.01 Detailed Business Requirements
04.01.01 Enhancement Triggers
User will execute the “Smart Search” from Fiori Launch pad.
04.01.02 Screens & Wireframes
04.01.03 Detail Design
Process steps
Below are the process steps:
Execute the custom app “Smart Search”, fill in the search criteria, select “go” to get the search result.
Select one or multiple work package, click “Add to my favorite”. Work package will be added to project “Team” section. As a result, the work package will show up in the “favorite” section of “Manage My Timesheet”.
Users will submit timesheet in “Manage My Timesheet”.
Customer Project (Engagement Project) — Simplified Flow:
Salesforce Opportunity → Customer Creation (CPVT) → Project Creation (CPVT) → Maintain Work Packages & Pricing → Staff Resources (Smart Search) → Record Time → Billing (BAPA) → Revenue Recognition → Payment & AR Clearing → Close Project
Enterprise Project — Simplified Flow:
Create Project → Define WBS → Release Project → Assign Settlement Rule → Allocate Budget → * →Record Time & Expenses → Monitor Project → Period-End Closing (Overhead + Settlement) → Complete Project → Close Project

*Smart search for enterprise project will be added based during FSD update. As of now, this item is under CR list.
Internal Project (Engagement Project) — Simplified Flow:
Setup → Create Internal Project → Maintain Work Packages → Staff Resources (Smart Search) → Record Time → Monitor Costs → Settle → Close Project.
No.
Field
Mandatory/ Optional
Remark
1
Customer Name
M
<Update Start Date> May 5, 2026- Strikethrough done because customer name field is no longer mandatory in adapt filter. This helped to extract all the projects and details when required.
This is mandatory because staff should know the customer.
<Update End Date> May 5, 2026
 
Be able to search sold-to by company code of sold-to with multiple selection of the company codes.
2
Project Manager
O
 
3
Customer Delivery Market (CDM)
O
 
4
Project Name
O
 
5
Work Package Name
O
 
6
Product Group
O
 
7
Product
O
 
8
Project Header Profit Center
O
Deferred to wave 1.1
9
Work Package Profit Center
O
Deferred to wave 1.1
Product and client delivery–related fields (Product Code, Product Description, Product Group Code/Name, Client Delivery Market) are applicable only to Customer Projects and are out of scope for Internal Projects (1A8), as internal projects are non-billable and not client related.
Search Criteria
Below search criteria will be available on the screen.
The hit list will show below columns with default sorting
No.
Field
Sort
Remark
1
Customer
1st level sort
<Not applicable for internal projects>
2
Project Name
2nd level sort
 
3
Customer Delivery Market (CDM)
 
 
4
Project Header Profit Center Code
 
Deferred to wave 1.1
5
Project Header Profit Center Description
 
Deferred to wave 1.1
6
Work Package Name
 
 
7
Product
 
 
8
Work Package Profit Center Code
 
Deferred to wave 1.1
9
Work Package Profit Center Description
 
Deferred to wave 1.1
 
From the hit list, user will select one or more work package and click “Add to my favorite”
By clicking “Add to my favorite”, the user will be added to project “Team” section. The purpose of this: the work package will show up in the “favorite” section of “Manage My Timesheet”.

Below are the fields required to add the user to the Team section (resource assignment)
 
Field
Logic
Remark
Role (Activity Type)
It will be translated from Service Cost Level from Work Agreement.
Refer to below logic how to get the active Work Agreement.
Delivery Organization
Mapped to “Company Code” in the Work Agreement.
 
Refer to below logic how to get the active Work Agreement.
Work Package
Based on the project + work packages that user selected.
 
Resource
Mapped to “Personnel Number”
Refer to below logic how to get the active Work Agreement.
Effort
Default to 1 hour
 
Confirmed Status
Default to Yes.
 
 
 
Below are the logics to determine the value for:
Role
Delivery Organization
Personnel Number (Employment ID)
 
Step 1: Determine the Worker ID
In Manage Work Agreement app, get the “Worker ID” where “User ID” = current user.
Step 2: Determine the Work Agreement
There will be multiple Work Agreements for a given Worker.
To determine the active Work Agreement, use below logic.
Employment Situation = “Initial Employment (I)” or “Transfer or Rehire (T)”
Current date: within Start Date and Date (valid entry)
 
Step 3: Determine the Role (Activity Type)
Select the active Service Cost Level
 

 
Determine the Role (Activity Type) from the parameter table. This value is unlikely to change. Thus there will not be maintenance view. However, should there be a need to change, business users can request IT to change the parameter table.
Service Cost Level Code
Service Cost Levels
Role (Activity Type)

Service Cost Level Code
Service Cost Levels
Role (Activity Type)
0001
Associate A1
T001
0002
Senior Associate A2
T002
0003
Assistant Manager A3
T003
0004
Manager B1
T004
0005
Senior Manager B2
T005
0006
Director C1
T006
0007
Executive Director C2
T007
0008
Vice President or Senior Vice President D1
T008
0009
Executive Vice President D2
T009
 
Step 4: Determine Delivery Organization and Resource
Delivery Organization = Company Code
Resource: Personnel Number (aka Employment ID)
 
Once user is added to the project resource assignment with desired work package, the work package will be added to the favorite of the “Manage My Timesheet”.

Key Design on the job grade change requirement:
Job grade change will be interfaced from SuFa to S4 as part of I003 - employee replication interface and update the service cost level in manage workforce with the validity.
When an employee tries to book and submit the time entries in manage my timesheet app by choosing the work package with the previous job grade, message throws to reassign the work package in smart search.
Error Message “Job Grade Updated. Assign the work package in Smart Search”
Validation will check Timesheet date + Activity Type at the work package VS Service Cost Level date range + service cost level
Employee logon to smart search app - choose the existing work package and click “Assign To Me”. New role with the updated job grade will be added to the project work package to appear them in tasks fav.
Manage my timesheet app will show the existing work package with the new job grade. Employee to choose the work package with the correct job grade (activity type) for the time entries.
Standard “sort by recently used” function in tasks fav will make the old job grade based work package to disappear.
Above design is applicable only for the customer projects.
Assumption: Timesheet entries should be allowed only the planned roles (activity) at work package level or direct task added in manage my timesheet should match with service cost level of an employee. No exception.