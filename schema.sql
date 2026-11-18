-- 全童全星上课点名系统 数据库结构（由现有 dmxt 库导出）
-- 初始化：mysql -u root -p < schema.sql
-- 注意：只建表，不含任何学生/签到数据。

CREATE DATABASE IF NOT EXISTS `dmxt`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `dmxt`;

CREATE TABLE `t_class` (
  `class_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `class_type` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `class_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `class_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `class_hour` double DEFAULT NULL,
  `class_val` int DEFAULT NULL,
  `class_day` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`class_id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;

CREATE TABLE `t_stu` (
  `stu_id` int NOT NULL AUTO_INCREMENT,
  `stu_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `stu_class_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `stu_hour` double DEFAULT NULL,
  `phone` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `stu_state` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '有效标志：0已删除(记录保留,可恢复),1正常,2停课,3结课',
  PRIMARY KEY (`stu_id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;

-- 学生-班级映射：学生全局唯一（t_stu 一人一条），归属关系在这里，
-- 一个学生可以映射到多个班级。程序首次运行会自动建表并迁移旧数据
-- （把按班重复的旧学生行合并成主档 + 映射）。
CREATE TABLE IF NOT EXISTS `t_stu_class` (
  `map_id` int NOT NULL AUTO_INCREMENT,
  `stu_id` int NOT NULL COMMENT '学生主档 t_stu.stu_id',
  `class_id` varchar(64) NOT NULL COMMENT '班级编号 t_class.class_id',
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`map_id`),
  UNIQUE KEY `uk_stu_class` (`stu_id`, `class_id`),
  KEY `idx_class` (`class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `t_sign` (
  `dm_id` int NOT NULL AUTO_INCREMENT,
  `stu_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `stu_class_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `sign_hour` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `sign_state` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `sign_time` varchar(255) DEFAULT NULL,
  `bz` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`dm_id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;

-- ---------------------------------------------------------------- 暑假 / 寒假课程
-- 假期班上课日期不固定，点名按「课次」走，所以单独一套表，与上面的常规班互不影响。
-- 程序第一次打开「暑假课程 / 寒假课程」时会自动建这四张表（CREATE TABLE IF NOT EXISTS），
-- 这里同步一份，方便手工初始化。

CREATE TABLE IF NOT EXISTS `t_season_class` (
  `sclass_id` int NOT NULL AUTO_INCREMENT,
  `season` varchar(16) NOT NULL COMMENT 'summer 暑假 / winter 寒假',
  `class_name` varchar(100) NOT NULL COMMENT '班级名称，老师自定义',
  `class_type` varchar(100) DEFAULT NULL COMMENT '课程内容，如 C++ 集训',
  `class_hour` double DEFAULT 2.5 COMMENT '每次课默认课时',
  `start_date` date DEFAULT NULL,
  `end_date` date DEFAULT NULL,
  `note` varchar(255) DEFAULT NULL,
  `state` varchar(8) NOT NULL DEFAULT '1' COMMENT '有效标志：0已删除(可恢复),1有效',
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`sclass_id`),
  KEY `idx_season_state` (`season`, `state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_season_stu` (
  `sstu_id` int NOT NULL AUTO_INCREMENT,
  `sclass_id` int NOT NULL COMMENT '所属假期班级',
  `stu_name` varchar(50) NOT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `note` varchar(255) DEFAULT NULL,
  `state` varchar(8) NOT NULL DEFAULT '1' COMMENT '有效标志：0已删除(可恢复),1有效',
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`sstu_id`),
  KEY `idx_class_state` (`sclass_id`, `state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_season_lesson` (
  `lesson_id` int NOT NULL AUTO_INCREMENT,
  `sclass_id` int NOT NULL COMMENT '所属假期班级',
  `lesson_date` date NOT NULL COMMENT '上课日期',
  `lesson_time` varchar(60) DEFAULT NULL COMMENT '时间段，如 14:00-16:00',
  `topic` varchar(200) DEFAULT NULL COMMENT '本次课内容',
  `note` varchar(255) DEFAULT NULL,
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`lesson_id`),
  KEY `idx_class_date` (`sclass_id`, `lesson_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_season_sign` (
  `sign_id` int NOT NULL AUTO_INCREMENT,
  `lesson_id` int NOT NULL,
  `sclass_id` int NOT NULL,
  `sstu_id` int NOT NULL,
  `stu_name` varchar(50) NOT NULL COMMENT '冗余姓名，导出的表格直接可读',
  `sign_hour` varchar(20) DEFAULT NULL COMMENT '本次课计课时',
  `sign_state` varchar(8) NOT NULL DEFAULT '0' COMMENT '1已签到 0未签到',
  `sign_time` varchar(40) DEFAULT NULL,
  `bz` varchar(255) DEFAULT NULL,
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`sign_id`),
  UNIQUE KEY `uk_lesson_stu` (`lesson_id`, `sstu_id`),
  KEY `idx_class` (`sclass_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------- 积分 / 积分商城
-- 积分余额不存字段，等于 t_point_record 里该学生所有 delta 之和；
-- 商城兑换会写一笔负数流水，所以两个页面的账永远一致。
-- 程序第一次打开「积分管理 / 积分商城」时会自动建这三张表（IF NOT EXISTS），
-- 恢复旧备份后也会自动补建，这里同步一份方便手工初始化。

CREATE TABLE IF NOT EXISTS `t_point_record` (
  `record_id` int NOT NULL AUTO_INCREMENT,
  `stu_id` varchar(32) NOT NULL COMMENT '学生主档 id',
  `stu_name` varchar(50) NOT NULL COMMENT '当时姓名快照',
  `class_id` varchar(32) NOT NULL COMMENT '当时班级快照',
  `delta` int NOT NULL COMMENT '变动分值，正加负减',
  `balance_after` int NOT NULL DEFAULT 0 COMMENT '本笔之后的余额（便于对账）',
  `note` varchar(255) DEFAULT NULL,
  `create_time` varchar(32) NOT NULL,
  PRIMARY KEY (`record_id`),
  KEY `idx_point_stu` (`stu_id`),
  KEY `idx_point_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_goods` (
  `goods_id` int NOT NULL AUTO_INCREMENT COMMENT '商品主键',
  `goods_name` varchar(100) NOT NULL,
  `cost` int NOT NULL COMMENT '兑换所需积分（单件）',
  `stock` int NOT NULL DEFAULT -1 COMMENT '剩余库存，-1 表示不限量',
  `note` varchar(255) DEFAULT NULL COMMENT '商品说明',
  `state` varchar(8) NOT NULL DEFAULT '1' COMMENT '1 上架 / 0 下架',
  `create_time` varchar(32) NOT NULL,
  PRIMARY KEY (`goods_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_exchange` (
  `exchange_id` int NOT NULL AUTO_INCREMENT,
  `stu_id` varchar(32) NOT NULL,
  `stu_name` varchar(50) NOT NULL COMMENT '当时姓名快照',
  `class_id` varchar(32) NOT NULL COMMENT '当时班级快照',
  `goods_id` int NOT NULL,
  `goods_name` varchar(100) NOT NULL COMMENT '商品名快照',
  `cost` int NOT NULL COMMENT '单件积分快照',
  `qty` int NOT NULL COMMENT '兑换数量',
  `total_cost` int NOT NULL COMMENT '本次共扣积分',
  `note` varchar(255) DEFAULT NULL,
  `create_time` varchar(32) NOT NULL,
  PRIMARY KEY (`exchange_id`),
  KEY `idx_ex_stu` (`stu_id`),
  KEY `idx_ex_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
